from __future__ import annotations

import asyncio
import os
import tempfile
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from ra_agent.contracts import ExecutionStatus, RollbackResult
from ra_agent.tools.path_resolver import SafePathResolver

from .checkpoint import (
    BackupRecord,
    CheckpointRecord,
    CheckpointStatus,
    FilesystemCheckpointManager,
)
from .pending_store import (
    PendingNotFoundError,
    PendingOperation,
    PendingRecord,
    PendingStatus,
    PendingStore,
)


class RollbackManagerError(RuntimeError):
    """Base error raised by filesystem rollback operations."""


class RollbackCorrelationError(RollbackManagerError):
    """Raised when checkpoint and pending metadata do not describe one request."""


class RollbackIntegrityError(RollbackManagerError):
    """Raised when checkpoint or pending recovery data is corrupted."""


class RollbackConflictError(RollbackManagerError):
    """Raised when a workspace path can no longer be restored safely."""


class UnsupportedRollbackOperationError(RollbackManagerError):
    """Raised when checkpoint metadata describes an unsupported operation."""


class RollbackManager(Protocol):
    async def rollback(
        self,
        checkpoint_id: str,
        request_id: str,
    ) -> RollbackResult: ...


class FilesystemRollbackManager:
    """Restore the workspace to the state captured by a filesystem checkpoint."""

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

    async def rollback(
        self,
        checkpoint_id: str,
        request_id: str,
    ) -> RollbackResult:
        """Restore one checkpoint and clean its pending change idempotently."""

        async with self._lock:
            checkpoint = await self._checkpoint_manager.get(checkpoint_id)
            self._validate_checkpoint_identity(
                checkpoint,
                checkpoint_id,
                request_id,
            )

            if not await self._checkpoint_manager.verify_integrity(checkpoint_id):
                raise RollbackIntegrityError("checkpoint failed integrity verification")

            if checkpoint.status is CheckpointStatus.ROLLED_BACK:
                await self._pending_store.cleanup(request_id)
                return self._result(
                    checkpoint_id,
                    request_id,
                    "filesystem rollback already completed",
                )

            pending = await self._load_pending(request_id)
            self._validate_records(checkpoint, pending)

            backup = checkpoint.backups[0]
            backup_payload: bytes | None = None

            if backup.existed:
                if backup.backup_path is None:
                    raise RollbackIntegrityError(
                        "existing checkpoint backup is missing backup_path"
                    )

                try:
                    backup_payload = await self._checkpoint_manager.read_backup(
                        checkpoint_id,
                        backup.backup_path,
                    )
                except Exception as error:
                    raise RollbackIntegrityError(
                        "checkpoint backup could not be read safely"
                    ) from error

            await asyncio.to_thread(
                self._restore_sync,
                checkpoint,
                backup,
                backup_payload,
            )

            await self._pending_store.cleanup(request_id)
            await self._checkpoint_manager.mark_rolled_back(checkpoint_id)

            return self._result(
                checkpoint_id,
                request_id,
                "filesystem state restored from checkpoint",
            )

    async def _load_pending(
        self,
        request_id: str,
    ) -> PendingRecord | None:
        try:
            pending = await self._pending_store.get(request_id)
        except PendingNotFoundError:
            return None

        if not await self._pending_store.verify_integrity(request_id):
            raise RollbackIntegrityError("pending data failed integrity verification")

        return pending

    @staticmethod
    def _validate_checkpoint_identity(
        checkpoint: CheckpointRecord,
        checkpoint_id: str,
        request_id: str,
    ) -> None:
        if checkpoint.checkpoint_id != checkpoint_id:
            raise RollbackCorrelationError(
                "checkpoint manifest checkpoint_id does not match rollback request"
            )

        if checkpoint.request_id != request_id:
            raise RollbackCorrelationError(
                "checkpoint manifest request_id does not match rollback request"
            )

        if checkpoint.status not in {
            CheckpointStatus.CREATED,
            CheckpointStatus.COMMITTED,
            CheckpointStatus.ROLLED_BACK,
        }:
            raise RollbackCorrelationError(f"unsupported checkpoint status: {checkpoint.status}")

        if len(checkpoint.target_paths) != 1 or len(checkpoint.backups) != 1:
            raise RollbackCorrelationError(
                "filesystem rollback requires exactly one checkpoint target"
            )

        backup = checkpoint.backups[0]

        if checkpoint.target_paths != (backup.target_path,):
            raise RollbackCorrelationError("checkpoint target_paths do not match backup metadata")

        if checkpoint.tool_name not in {"write_file", "delete_file"}:
            raise UnsupportedRollbackOperationError(
                f"unsupported checkpoint tool: {checkpoint.tool_name}"
            )

        if checkpoint.tool_name == "delete_file" and not backup.existed:
            raise RollbackCorrelationError("delete checkpoint must contain an existing-file backup")

    @staticmethod
    def _validate_records(
        checkpoint: CheckpointRecord,
        pending: PendingRecord | None,
    ) -> None:
        if pending is None:
            return

        if pending.request_id != checkpoint.request_id:
            raise RollbackCorrelationError("pending request_id does not match checkpoint")

        if pending.checkpoint_id not in {
            None,
            checkpoint.checkpoint_id,
        }:
            raise RollbackCorrelationError("pending checkpoint_id does not match checkpoint")

        if pending.tool_name != checkpoint.tool_name:
            raise RollbackCorrelationError("pending tool_name does not match checkpoint")

        if pending.target_path != checkpoint.target_paths[0]:
            raise RollbackCorrelationError("pending target_path does not match checkpoint")

        expected_operation = {
            "write_file": PendingOperation.WRITE,
            "delete_file": PendingOperation.DELETE,
        }[checkpoint.tool_name]

        if pending.operation is not expected_operation:
            raise RollbackCorrelationError("pending operation does not match checkpoint tool")

        if pending.status not in {
            PendingStatus.PENDING,
            PendingStatus.COMMITTED,
        }:
            raise RollbackCorrelationError(f"unsupported pending status: {pending.status}")

    def _restore_sync(
        self,
        checkpoint: CheckpointRecord,
        backup: BackupRecord,
        backup_payload: bytes | None,
    ) -> None:
        try:
            target = self._path_resolver.resolve_write_target(backup.target_path)
        except Exception as error:
            raise RollbackConflictError(
                "rollback target is no longer a safe workspace path"
            ) from error

        actual_relative = self._path_resolver.to_relative(target)

        if actual_relative != backup.target_path:
            raise RollbackCorrelationError("resolved rollback target does not match checkpoint")

        if backup.existed:
            if (
                backup_payload is None
                or backup.sha256 is None
                or backup.size_bytes is None
                or backup.mtime_ns is None
                or backup.mode is None
            ):
                raise RollbackIntegrityError("checkpoint backup is missing restoration metadata")

            if len(backup_payload) != backup.size_bytes:
                raise RollbackIntegrityError("checkpoint backup size does not match manifest")

            if sha256(backup_payload).hexdigest() != backup.sha256:
                raise RollbackIntegrityError("checkpoint backup hash does not match manifest")

            self._restore_file_atomically(
                target,
                backup_payload,
                mode=backup.mode,
                mtime_ns=backup.mtime_ns,
            )
        else:
            self._remove_created_target(target)

        self._cleanup_commit_temp_files(target)

    @staticmethod
    def _restore_file_atomically(
        target: Path,
        payload: bytes,
        *,
        mode: int,
        mtime_ns: int,
    ) -> None:
        temporary_name: str | None = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{target.name}.rollback.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name

            if temporary_name is None:
                raise RollbackIntegrityError("temporary rollback file was not created")

            temporary_path = Path(temporary_name)
            os.chmod(temporary_path, mode)
            os.replace(temporary_path, target)
            temporary_name = None
            os.utime(
                target,
                ns=(mtime_ns, mtime_ns),
            )
            FilesystemRollbackManager._fsync_directory(target.parent)
        finally:
            if temporary_name is not None:
                temporary_path = Path(temporary_name)

                if temporary_path.exists():
                    temporary_path.unlink()

    @staticmethod
    def _remove_created_target(target: Path) -> None:
        if not target.exists() and not target.is_symlink():
            return

        if target.is_symlink() or not target.is_file():
            raise RollbackConflictError("rollback target is not a regular file")

        target.unlink()
        FilesystemRollbackManager._fsync_directory(target.parent)

    @staticmethod
    def _cleanup_commit_temp_files(target: Path) -> None:
        prefix = f".{target.name}."
        suffix = ".tmp"

        for child in target.parent.iterdir():
            if not child.name.startswith(prefix) or not child.name.endswith(suffix):
                continue

            if child.is_symlink() or not child.is_file():
                continue

            child.unlink()

        FilesystemRollbackManager._fsync_directory(target.parent)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return

        descriptor = os.open(directory, os.O_RDONLY)

        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _result(
        checkpoint_id: str,
        request_id: str,
        reason: str,
    ) -> RollbackResult:
        return RollbackResult(
            request_id=request_id,
            checkpoint_id=checkpoint_id,
            status=ExecutionStatus.ROLLED_BACK,
            reason=reason,
        )


class MockRollbackManager:
    """No-side-effect rollback mock for orchestration tests."""

    async def rollback(
        self,
        checkpoint_id: str,
        request_id: str,
    ) -> RollbackResult:
        return RollbackResult(
            request_id=request_id,
            checkpoint_id=checkpoint_id,
            status=ExecutionStatus.ROLLED_BACK,
            reason="mock rollback completed",
        )
