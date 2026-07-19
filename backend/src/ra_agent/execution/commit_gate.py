from __future__ import annotations

import asyncio
import os
import tempfile
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Protocol

from ra_agent.contracts import (
    CommitResult,
    DeepCheckResult,
    ExecutionStatus,
    ToolExecutionResult,
)
from ra_agent.tools.path_resolver import SafePathResolver

from ._temp_files import transaction_temp_token
from .checkpoint import (
    BackupRecord,
    CheckpointRecord,
    CheckpointStatus,
    FilesystemCheckpointManager,
)
from .pending_store import (
    PendingOperation,
    PendingRecord,
    PendingStatus,
    PendingStore,
)


class CommitGateError(RuntimeError):
    """Base error for rejected or failed filesystem commits."""


class CommitPreconditionError(CommitGateError):
    """Raised when execution or deep-check state is not committable."""


class CommitCorrelationError(CommitGateError):
    """Raised when request, checkpoint, tool, or target metadata disagree."""


class CommitIntegrityError(CommitGateError):
    """Raised when pending or checkpoint data fails integrity checks."""


class CommitConflictError(CommitGateError):
    """Raised when the trusted workspace changed after checkpoint creation."""


class UnsupportedCommitOperationError(CommitGateError):
    """Raised when a pending operation cannot be committed."""


class CommitGate(Protocol):
    async def commit(
        self,
        execution: ToolExecutionResult,
        deep_check: DeepCheckResult,
    ) -> CommitResult: ...


class FilesystemCommitGate:
    """Atomically apply verified pending file changes to the workspace."""

    def __init__(
        self,
        path_resolver: SafePathResolver,
        pending_store: PendingStore,
        checkpoint_manager: FilesystemCheckpointManager,
    ) -> None:
        self._path_resolver = path_resolver
        self._pending_store = pending_store
        self._checkpoint_manager = checkpoint_manager
        self._lock = asyncio.Lock()

    async def commit(
        self,
        execution: ToolExecutionResult,
        deep_check: DeepCheckResult,
    ) -> CommitResult:
        """Validate and commit one pending write or delete operation."""

        self._validate_execution(execution, deep_check)
        checkpoint_id = execution.checkpoint_id

        if checkpoint_id is None:
            raise CommitPreconditionError("PENDING_COMMIT execution requires a checkpoint_id")

        async with self._lock:
            pending = await self._pending_store.get(execution.request_id)
            checkpoint = await self._checkpoint_manager.get(checkpoint_id)

            self._validate_records(
                execution,
                deep_check,
                pending,
                checkpoint,
            )

            if not await self._pending_store.verify_integrity(execution.request_id):
                raise CommitIntegrityError("pending data failed integrity verification")

            if not await self._checkpoint_manager.verify_integrity(checkpoint_id):
                raise CommitIntegrityError("checkpoint failed integrity verification")

            if (
                pending.status is PendingStatus.COMMITTED
                and checkpoint.status is CheckpointStatus.COMMITTED
            ):
                return self._committed_result(execution)

            if (
                pending.status is not PendingStatus.PENDING
                or checkpoint.status is not CheckpointStatus.CREATED
            ):
                raise CommitPreconditionError(
                    "pending and checkpoint commit states are inconsistent"
                )

            backup = checkpoint.backups[0]

            if pending.operation is PendingOperation.WRITE:
                await asyncio.to_thread(
                    self._commit_write_sync,
                    pending,
                    backup,
                )
            elif pending.operation is PendingOperation.DELETE:
                await asyncio.to_thread(
                    self._commit_delete_sync,
                    pending,
                    backup,
                )
            else:
                raise UnsupportedCommitOperationError(
                    f"unsupported pending operation: {pending.operation}"
                )

            await self._pending_store.mark_committed(execution.request_id)
            await self._checkpoint_manager.mark_committed(checkpoint_id)

            return self._committed_result(execution)

    @staticmethod
    def _validate_execution(
        execution: ToolExecutionResult,
        deep_check: DeepCheckResult,
    ) -> None:
        if execution.status is not ExecutionStatus.PENDING_COMMIT:
            raise CommitPreconditionError("execution status must be PENDING_COMMIT")

        if not deep_check.passed:
            raise CommitPreconditionError("deep safety check must pass before commit")

        if execution.request_id != deep_check.request_id:
            raise CommitCorrelationError("execution and deep-check request_id do not match")

    @staticmethod
    def _validate_records(
        execution: ToolExecutionResult,
        deep_check: DeepCheckResult,
        pending: PendingRecord,
        checkpoint: CheckpointRecord,
    ) -> None:
        if not (
            execution.request_id
            == deep_check.request_id
            == pending.request_id
            == checkpoint.request_id
        ):
            raise CommitCorrelationError("request_id does not match across commit records")

        if not (execution.checkpoint_id == pending.checkpoint_id == checkpoint.checkpoint_id):
            raise CommitCorrelationError("checkpoint_id does not match across commit records")

        if pending.tool_name != checkpoint.tool_name:
            raise CommitCorrelationError("pending and checkpoint tool_name do not match")

        expected_tool = {
            PendingOperation.WRITE: "write_file",
            PendingOperation.DELETE: "delete_file",
        }.get(pending.operation)

        if expected_tool is None:
            raise UnsupportedCommitOperationError(
                f"unsupported pending operation: {pending.operation}"
            )

        if pending.tool_name != expected_tool:
            raise CommitCorrelationError("pending operation does not match tool_name")

        if checkpoint.target_paths != (pending.target_path,):
            raise CommitCorrelationError(
                "pending target_path does not match checkpoint target_paths"
            )

        if len(checkpoint.backups) != 1:
            raise CommitCorrelationError("filesystem commit requires exactly one checkpoint backup")

        if checkpoint.backups[0].target_path != pending.target_path:
            raise CommitCorrelationError(
                "checkpoint backup target_path does not match pending target"
            )

    def _commit_write_sync(
        self,
        pending: PendingRecord,
        backup: BackupRecord,
    ) -> None:
        if (
            pending.pending_path is None
            or pending.content_sha256 is None
            or pending.size_bytes is None
        ):
            raise CommitIntegrityError("write pending record is missing payload metadata")

        try:
            target = self._path_resolver.resolve_write_target(pending.target_path)
        except Exception as error:
            raise CommitConflictError(
                "write target is no longer a safe workspace target"
            ) from error

        self._validate_resolved_target(target, pending.target_path)
        self._verify_write_target_unchanged(target, backup)

        payload_path = self._resolve_pending_payload(pending.pending_path)
        payload = payload_path.read_bytes()

        if len(payload) != pending.size_bytes:
            raise CommitIntegrityError("pending payload size does not match manifest")

        if sha256(payload).hexdigest() != pending.content_sha256:
            raise CommitIntegrityError("pending payload hash does not match manifest")

        self._atomic_replace_target(
            target,
            payload,
            request_id=pending.request_id,
            existing_mode=backup.mode if backup.existed else None,
            reserve_new_name=not backup.existed,
        )

    def _commit_delete_sync(
        self,
        pending: PendingRecord,
        backup: BackupRecord,
    ) -> None:
        if not backup.existed:
            raise CommitCorrelationError("delete checkpoint must contain an existing-file backup")

        if backup.sha256 is None or backup.size_bytes is None:
            raise CommitIntegrityError("delete checkpoint is missing original file metadata")

        try:
            target = self._path_resolver.resolve_delete_target(pending.target_path)
        except Exception as error:
            raise CommitConflictError(
                "delete target is no longer the checkpointed regular file"
            ) from error

        self._validate_resolved_target(target, pending.target_path)
        payload, identity = self._read_stable_file(target)

        if len(payload) != backup.size_bytes:
            raise CommitConflictError("delete target size changed after checkpoint creation")

        if sha256(payload).hexdigest() != backup.sha256:
            raise CommitConflictError("delete target content changed after checkpoint creation")

        current = target.stat()
        current_identity = self._file_identity(current)

        if current_identity != identity or target.is_symlink():
            raise CommitConflictError("delete target changed immediately before deletion")

        target.unlink()
        self._fsync_directory(target.parent)

    def _verify_write_target_unchanged(
        self,
        target: Path,
        backup: BackupRecord,
    ) -> None:
        if backup.existed:
            if backup.sha256 is None or backup.size_bytes is None:
                raise CommitIntegrityError("existing-file checkpoint is missing original metadata")

            if not target.exists() or target.is_symlink() or not target.is_file():
                raise CommitConflictError("write target no longer matches checkpoint state")

            payload, _ = self._read_stable_file(target)

            if len(payload) != backup.size_bytes:
                raise CommitConflictError("write target size changed after checkpoint creation")

            if sha256(payload).hexdigest() != backup.sha256:
                raise CommitConflictError("write target content changed after checkpoint creation")
        elif target.exists() or target.is_symlink():
            raise CommitConflictError("new write target was created after checkpoint creation")

    def _resolve_pending_payload(
        self,
        pending_path: str,
    ) -> Path:
        pure_path = PurePosixPath(pending_path)

        if pure_path.is_absolute() or ".." in pure_path.parts:
            raise CommitIntegrityError("pending payload path is unsafe")

        candidate = self._pending_store.pending_root / Path(*pure_path.parts)

        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            raise CommitIntegrityError("pending payload does not exist") from error

        self._ensure_within_root(
            resolved,
            self._pending_store.pending_root,
            "pending payload",
        )

        if not resolved.is_file() or resolved.is_symlink():
            raise CommitIntegrityError("pending payload must be a regular file")

        return resolved

    def _validate_resolved_target(
        self,
        target: Path,
        expected_relative_path: str,
    ) -> None:
        actual_relative = self._path_resolver.to_relative(target)

        if actual_relative != expected_relative_path:
            raise CommitCorrelationError("resolved target does not match pending target_path")

    @staticmethod
    def _read_stable_file(
        target: Path,
    ) -> tuple[bytes, tuple[int, int, int, int]]:
        before = target.stat()
        payload = target.read_bytes()
        after = target.stat()

        before_identity = FilesystemCommitGate._file_identity(before)
        after_identity = FilesystemCommitGate._file_identity(after)

        if before_identity != after_identity or len(payload) != after.st_size:
            raise CommitConflictError("workspace target changed while being verified")

        return payload, after_identity

    @staticmethod
    def _file_identity(stat_result: os.stat_result) -> tuple[int, int, int, int]:
        return (
            stat_result.st_dev,
            stat_result.st_ino,
            stat_result.st_size,
            stat_result.st_mtime_ns,
        )

    @staticmethod
    def _atomic_replace_target(
        target: Path,
        payload: bytes,
        *,
        request_id: str,
        existing_mode: int | None,
        reserve_new_name: bool,
    ) -> None:
        temporary_name: str | None = None
        reservation_created = False

        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{target.name}.{transaction_temp_token(request_id)}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name

            if temporary_name is None:
                raise CommitIntegrityError("temporary commit file was not created")

            temporary_path = Path(temporary_name)

            if existing_mode is not None:
                os.chmod(temporary_path, existing_mode)

            if reserve_new_name:
                try:
                    descriptor = os.open(
                        target,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                except FileExistsError as error:
                    raise CommitConflictError(
                        "new write target was created before commit"
                    ) from error
                else:
                    os.close(descriptor)
                    reservation_created = True

            os.replace(temporary_path, target)
            temporary_name = None
            reservation_created = False
            FilesystemCommitGate._fsync_directory(target.parent)
        finally:
            if temporary_name is not None:
                temporary_path = Path(temporary_name)

                if temporary_path.exists():
                    temporary_path.unlink()

            if reservation_created and target.exists():
                target.unlink()

    @staticmethod
    def _ensure_within_root(
        path: Path,
        root: Path,
        description: str,
    ) -> None:
        root_text = os.path.normcase(os.path.abspath(root))
        path_text = os.path.normcase(os.path.abspath(path))

        try:
            common = os.path.commonpath((root_text, path_text))
        except ValueError as error:
            raise CommitIntegrityError(f"{description} is on another drive") from error

        if common != root_text:
            raise CommitIntegrityError(f"{description} escapes its trusted root")

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)

        try:
            descriptor = os.open(directory, flags)
        except OSError:
            # Directory fsync is not available on every Windows filesystem.
            return

        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    @staticmethod
    def _committed_result(
        execution: ToolExecutionResult,
    ) -> CommitResult:
        return CommitResult(
            request_id=execution.request_id,
            checkpoint_id=execution.checkpoint_id,
            status=ExecutionStatus.COMMITTED,
        )


class MockCommitGate:
    """No-side-effect commit mock for orchestration tests."""

    async def commit(
        self,
        execution: ToolExecutionResult,
        deep_check: DeepCheckResult,
    ) -> CommitResult:
        return CommitResult(
            request_id=execution.request_id,
            checkpoint_id=execution.checkpoint_id,
            status=ExecutionStatus.COMMITTED,
        )
