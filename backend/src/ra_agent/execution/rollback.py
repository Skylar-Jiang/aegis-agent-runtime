from __future__ import annotations

import asyncio
import os
import stat
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

from ra_agent.contracts import ExecutionStatus, RollbackResult
from ra_agent.tools.path_resolver import SafePathResolver

from ._temp_files import transaction_temp_token
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


@dataclass(frozen=True, slots=True)
class CommittedEffectState:
    """Expected trusted-workspace state for a committed filesystem effect."""

    operation: PendingOperation
    content_sha256: str | None = None
    size_bytes: int | None = None


class RollbackManager(Protocol):
    async def rollback(
        self,
        checkpoint_id: str,
        request_id: str,
        *,
        committed_effect: CommittedEffectState | None = None,
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
        *,
        committed_effect: CommittedEffectState | None = None,
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
            if pending is None and committed_effect is not None:
                pending = self._pending_from_committed_effect(
                    checkpoint,
                    committed_effect,
                )
            elif pending is not None and committed_effect is not None:
                self._validate_pending_against_committed_effect(
                    pending,
                    committed_effect,
                )
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
                pending,
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
    def _pending_from_committed_effect(
        checkpoint: CheckpointRecord,
        committed_effect: CommittedEffectState,
    ) -> PendingRecord:
        if checkpoint.status is not CheckpointStatus.COMMITTED:
            raise RollbackCorrelationError("committed effect state requires a COMMITTED checkpoint")

        expected_operation = {
            "write_file": PendingOperation.WRITE,
            "delete_file": PendingOperation.DELETE,
        }.get(checkpoint.tool_name)
        if expected_operation is None or committed_effect.operation is not expected_operation:
            raise RollbackCorrelationError(
                "committed effect operation does not match checkpoint tool"
            )

        content_sha256 = committed_effect.content_sha256
        size_bytes = committed_effect.size_bytes
        if committed_effect.operation is PendingOperation.WRITE:
            if (
                not isinstance(content_sha256, str)
                or len(content_sha256) != 64
                or any(character not in "0123456789abcdef" for character in content_sha256)
            ):
                raise RollbackIntegrityError(
                    "committed write effect requires a lowercase SHA-256 digest"
                )
            if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
                raise RollbackIntegrityError("committed write effect requires a non-negative size")
        elif content_sha256 is not None or size_bytes is not None:
            raise RollbackCorrelationError(
                "committed delete effect must not contain write payload metadata"
            )

        return PendingRecord(
            request_id=checkpoint.request_id,
            checkpoint_id=checkpoint.checkpoint_id,
            tool_name=checkpoint.tool_name,
            operation=committed_effect.operation,
            target_path=checkpoint.target_paths[0],
            pending_path=None,
            content_sha256=content_sha256,
            size_bytes=size_bytes,
            created_at=checkpoint.created_at,
            status=PendingStatus.COMMITTED,
        )

    @staticmethod
    def _validate_pending_against_committed_effect(
        pending: PendingRecord,
        committed_effect: CommittedEffectState,
    ) -> None:
        if pending.operation is not committed_effect.operation:
            raise RollbackCorrelationError("pending operation does not match committed effect")
        if pending.status is not PendingStatus.COMMITTED:
            raise RollbackCorrelationError("committed effect requires COMMITTED pending state")
        if committed_effect.operation is PendingOperation.WRITE:
            if (
                pending.content_sha256 != committed_effect.content_sha256
                or pending.size_bytes != committed_effect.size_bytes
            ):
                raise RollbackCorrelationError(
                    "pending payload metadata does not match committed effect"
                )
        elif pending.content_sha256 is not None or pending.size_bytes is not None:
            raise RollbackCorrelationError(
                "committed delete pending record contains write metadata"
            )

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
        pending: PendingRecord | None,
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

        if self._cleanup_interrupted_empty_quarantine(
            target,
            checkpoint.request_id,
            backup,
        ):
            self._cleanup_commit_temp_files(target, checkpoint.request_id)
            return

        quarantined = self._existing_quarantine(target, checkpoint.request_id)

        if quarantined is None:
            # Reject stable conflicts before taking custody so later user data
            # stays at its original path. Revalidate again after custody.
            if not self._matches_backup_state(target, backup) and not (
                self._matches_pending_state(target, pending)
            ):
                raise RollbackConflictError(
                    "rollback target no longer matches checkpoint or transaction state"
                )

            quarantined = self._take_target_custody(target, checkpoint.request_id)

        if not self._matches_backup_state(quarantined, backup) and not (
            self._matches_pending_state(quarantined, pending)
        ):
            raise RollbackConflictError(
                "quarantined rollback target does not match transaction state"
            )

        if target.exists() or target.is_symlink():
            if backup.existed and self._matches_backup_state(target, backup):
                self._discard_quarantine(quarantined)
                self._cleanup_commit_temp_files(target, checkpoint.request_id)
                return

            raise RollbackConflictError(
                "workspace target conflicts with interrupted rollback quarantine"
            )

        if backup.existed:
            if backup_payload is None or backup.mode is None or backup.mtime_ns is None:
                raise RollbackIntegrityError("checkpoint backup is missing restoration metadata")

            self._restore_file_atomically(
                target,
                backup_payload,
                request_id=checkpoint.request_id,
                mode=backup.mode,
                mtime_ns=backup.mtime_ns,
            )
            self._discard_quarantine(quarantined)
        else:
            self._discard_quarantine(quarantined)

            if target.exists() or target.is_symlink():
                raise RollbackConflictError("a concurrent target appeared during rollback")

        self._cleanup_commit_temp_files(target, checkpoint.request_id)

    @staticmethod
    def _matches_backup_state(target: Path | None, backup: BackupRecord) -> bool:
        if not backup.existed:
            return target is None or (not target.exists() and not target.is_symlink())

        if (
            backup.sha256 is None
            or backup.size_bytes is None
            or backup.mtime_ns is None
            or backup.mode is None
        ):
            raise RollbackIntegrityError("checkpoint backup is missing state metadata")

        return target is not None and FilesystemRollbackManager._matches_file_state(
            target,
            size_bytes=backup.size_bytes,
            digest=backup.sha256,
            mtime_ns=backup.mtime_ns,
            mode=backup.mode,
        )

    @staticmethod
    def _matches_pending_state(
        target: Path | None,
        pending: PendingRecord | None,
    ) -> bool:
        if pending is None:
            return False

        if pending.operation is PendingOperation.DELETE:
            return target is None or (not target.exists() and not target.is_symlink())

        if pending.content_sha256 is None or pending.size_bytes is None:
            raise RollbackIntegrityError("pending write is missing expected state metadata")

        return target is not None and FilesystemRollbackManager._matches_file_state(
            target,
            size_bytes=pending.size_bytes,
            digest=pending.content_sha256,
        )

    @staticmethod
    def _matches_file_state(
        target: Path,
        *,
        size_bytes: int,
        digest: str,
        mtime_ns: int | None = None,
        mode: int | None = None,
    ) -> bool:
        try:
            if target.is_symlink() or not target.is_file():
                return False

            before = target.stat()
            payload = target.read_bytes()
            after = target.stat()
        except OSError:
            return False

        return (
            FilesystemRollbackManager._file_identity(before)
            == FilesystemRollbackManager._file_identity(after)
            and len(payload) == size_bytes
            and sha256(payload).hexdigest() == digest
            and (mtime_ns is None or after.st_mtime_ns == mtime_ns)
            and (mode is None or stat.S_IMODE(after.st_mode) == mode)
        )

    @staticmethod
    def _file_identity(stat_result: os.stat_result) -> tuple[int, int, int, int]:
        return (
            stat_result.st_dev,
            stat_result.st_ino,
            stat_result.st_size,
            stat_result.st_mtime_ns,
        )

    @staticmethod
    def _existing_quarantine(target: Path, request_id: str) -> Path | None:
        token = transaction_temp_token(request_id)
        quarantine_dir = target.parent / f".{target.name}.{token}.rollback-quarantine"

        if not quarantine_dir.exists() and not quarantine_dir.is_symlink():
            return None

        quarantined = quarantine_dir / "target"

        if (
            quarantine_dir.is_symlink()
            or not quarantine_dir.is_dir()
            or quarantined.is_symlink()
            or not quarantined.is_file()
        ):
            raise RollbackConflictError("rollback quarantine is not recoverable")

        return quarantined

    @staticmethod
    def _cleanup_interrupted_empty_quarantine(
        target: Path,
        request_id: str,
        backup: BackupRecord,
    ) -> bool:
        token = transaction_temp_token(request_id)
        quarantine_dir = target.parent / f".{target.name}.{token}.rollback-quarantine"
        quarantined = quarantine_dir / "target"

        if not quarantine_dir.exists() and not quarantine_dir.is_symlink():
            return False
        if (
            quarantine_dir.is_symlink()
            or not quarantine_dir.is_dir()
            or quarantined.exists()
            or quarantined.is_symlink()
            or not FilesystemRollbackManager._matches_backup_state(target, backup)
        ):
            return False

        try:
            quarantine_dir.rmdir()
        except OSError:
            return False

        FilesystemRollbackManager._fsync_directory(target.parent)
        return True

    @staticmethod
    def _take_target_custody(target: Path, request_id: str) -> Path | None:
        token = transaction_temp_token(request_id)
        quarantine_dir = target.parent / f".{target.name}.{token}.rollback-quarantine"
        quarantined = quarantine_dir / "target"

        if quarantine_dir.exists():
            if target.exists() or target.is_symlink():
                raise RollbackConflictError("rollback quarantine and workspace target both exist")

            if quarantined.is_file() and not quarantined.is_symlink():
                return quarantined

            raise RollbackConflictError("rollback quarantine is not recoverable")

        quarantine_dir.mkdir(mode=0o700)

        try:
            os.replace(target, quarantined)
        except FileNotFoundError:
            quarantine_dir.rmdir()
            return None
        except OSError as error:
            try:
                quarantine_dir.rmdir()
            except OSError:
                pass
            raise RollbackConflictError("rollback could not take atomic target custody") from error

        FilesystemRollbackManager._fsync_directory(target.parent)
        return quarantined

    @staticmethod
    def _discard_quarantine(quarantined: Path | None) -> None:
        if quarantined is None:
            return

        try:
            quarantined.unlink()
        except FileNotFoundError:
            pass

        try:
            quarantined.parent.rmdir()
        except FileNotFoundError:
            pass

        FilesystemRollbackManager._fsync_directory(quarantined.parent.parent)

    @staticmethod
    def _restore_file_atomically(
        target: Path,
        payload: bytes,
        *,
        request_id: str,
        mode: int,
        mtime_ns: int,
    ) -> None:
        temporary_name: str | None = None
        preserve_temporary = False

        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=(f".{target.name}.{transaction_temp_token(request_id)}.rollback-backup."),
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
            os.utime(
                temporary_path,
                ns=(mtime_ns, mtime_ns),
            )

            try:
                os.link(temporary_path, target)
            except OSError as error:
                preserve_temporary = True
                raise RollbackConflictError(
                    "rollback backup could not be installed without overwrite"
                ) from error

            FilesystemRollbackManager._fsync_directory(target.parent)
        finally:
            if temporary_name is not None and not preserve_temporary:
                temporary_path = Path(temporary_name)

                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass

    @staticmethod
    def _cleanup_commit_temp_files(target: Path, request_id: str) -> None:
        prefix = f".{target.name}.{transaction_temp_token(request_id)}."
        suffix = ".tmp"

        for child in target.parent.iterdir():
            if not child.name.startswith(prefix) or not child.name.endswith(suffix):
                continue

            if child.is_symlink() or not child.is_file():
                continue

            try:
                child.unlink()
            except FileNotFoundError:
                continue

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
        *,
        committed_effect: CommittedEffectState | None = None,
    ) -> RollbackResult:
        return RollbackResult(
            request_id=request_id,
            checkpoint_id=checkpoint_id,
            status=ExecutionStatus.ROLLED_BACK,
            reason="mock rollback completed",
        )
