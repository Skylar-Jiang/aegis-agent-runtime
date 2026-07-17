from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import stat
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Protocol

from ra_agent.contracts import CheckpointResult, ExecutionStatus, ToolCallRequest
from ra_agent.tools.path_resolver import SafePathResolver

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class CheckpointManager(Protocol):
    async def create(self, request: ToolCallRequest) -> CheckpointResult: ...


class CheckpointStoreError(RuntimeError):
    """Base error raised by filesystem checkpoint operations."""


class CheckpointNotFoundError(CheckpointStoreError):
    """Raised when a checkpoint manifest cannot be found."""


class CheckpointConflictError(CheckpointStoreError):
    """Raised when an existing checkpoint has different request semantics."""


class CheckpointIntegrityError(CheckpointStoreError):
    """Raised when checkpoint metadata or backup data is invalid."""


class UnsupportedCheckpointToolError(CheckpointStoreError):
    """Raised when a tool does not support filesystem checkpointing."""


class CheckpointStatus(StrEnum):
    CREATED = "CREATED"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True, slots=True)
class BackupRecord:
    target_path: str
    existed: bool
    backup_path: str | None
    sha256: str | None
    size_bytes: int | None
    mtime_ns: int | None
    mode: int | None


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    checkpoint_id: str
    task_id: str
    step_id: str
    request_id: str
    tool_name: str
    target_paths: tuple[str, ...]
    created_at: datetime
    status: CheckpointStatus
    backups: tuple[BackupRecord, ...]


class FilesystemCheckpointManager:
    """Create durable file backups before pending write and delete operations."""

    MANIFEST_NAME = "manifest.json"
    MANIFEST_VERSION = 1
    BACKUP_DIRECTORY = "backups"

    def __init__(
        self,
        checkpoint_root: Path,
        path_resolver: SafePathResolver,
    ) -> None:
        if checkpoint_root.exists() and checkpoint_root.is_symlink():
            raise CheckpointStoreError("checkpoint root must not be a symbolic link")

        checkpoint_root.mkdir(parents=True, exist_ok=True)
        resolved_root = checkpoint_root.resolve(strict=True)

        if not resolved_root.is_dir():
            raise CheckpointStoreError("checkpoint root must be a directory")

        self._checkpoint_root = resolved_root
        self._path_resolver = path_resolver
        self._lock = asyncio.Lock()

    @property
    def checkpoint_root(self) -> Path:
        return self._checkpoint_root

    async def create(self, request: ToolCallRequest) -> CheckpointResult:
        """Create or safely reuse a checkpoint for one pending file operation."""

        async with self._lock:
            record = await asyncio.to_thread(self._create_sync, request)

        return CheckpointResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            checkpoint_id=record.checkpoint_id,
            status=ExecutionStatus.SUCCESS,
        )

    async def get(self, checkpoint_id: str) -> CheckpointRecord:
        """Load one checkpoint manifest."""

        self._validate_identifier(checkpoint_id, "checkpoint_id")

        async with self._lock:
            return await asyncio.to_thread(self._read_record_sync, checkpoint_id)

    async def verify_integrity(self, checkpoint_id: str) -> bool:
        """Verify manifest structure and all backup payload hashes."""

        self._validate_identifier(checkpoint_id, "checkpoint_id")

        async with self._lock:
            return await asyncio.to_thread(self._verify_integrity_sync, checkpoint_id)

    async def mark_committed(self, checkpoint_id: str) -> None:
        """Persist the COMMITTED state after a successful workspace commit."""

        self._validate_identifier(checkpoint_id, "checkpoint_id")

        async with self._lock:
            await asyncio.to_thread(
                self._mark_committed_sync,
                checkpoint_id,
            )

    async def mark_rolled_back(self, checkpoint_id: str) -> None:
        """Persist the ROLLED_BACK state after workspace recovery succeeds."""

        self._validate_identifier(checkpoint_id, "checkpoint_id")

        async with self._lock:
            await asyncio.to_thread(
                self._mark_rolled_back_sync,
                checkpoint_id,
            )

    async def read_backup(
        self,
        checkpoint_id: str,
        backup_path: str,
    ) -> bytes:
        """Read and verify one backup payload referenced by a checkpoint."""

        self._validate_identifier(checkpoint_id, "checkpoint_id")

        async with self._lock:
            return await asyncio.to_thread(
                self._read_backup_sync,
                checkpoint_id,
                backup_path,
            )

    def _mark_committed_sync(self, checkpoint_id: str) -> None:
        record = self._read_record_sync(checkpoint_id)

        if record.status is CheckpointStatus.COMMITTED:
            return

        if record.status is CheckpointStatus.ROLLED_BACK:
            raise CheckpointConflictError("a rolled-back checkpoint cannot be marked committed")

        if not self._verify_integrity_sync(checkpoint_id):
            raise CheckpointIntegrityError("checkpoint failed integrity verification")

        updated = replace(
            record,
            status=CheckpointStatus.COMMITTED,
        )
        self._write_record_sync(updated)

    def _mark_rolled_back_sync(self, checkpoint_id: str) -> None:
        record = self._read_record_sync(checkpoint_id)

        if record.status is CheckpointStatus.ROLLED_BACK:
            return

        if not self._verify_integrity_sync(checkpoint_id):
            raise CheckpointIntegrityError("checkpoint failed integrity verification")

        updated = replace(
            record,
            status=CheckpointStatus.ROLLED_BACK,
        )
        self._write_record_sync(updated)

    def _read_backup_sync(
        self,
        checkpoint_id: str,
        backup_path: str,
    ) -> bytes:
        normalized = self._validate_relative_file_path(
            backup_path,
            "backup_path",
        )
        record = self._read_record_sync(checkpoint_id)
        backup = next(
            (candidate for candidate in record.backups if candidate.backup_path == normalized),
            None,
        )

        if (
            backup is None
            or not backup.existed
            or backup.sha256 is None
            or backup.size_bytes is None
        ):
            raise CheckpointIntegrityError("checkpoint does not contain the requested backup")

        if not self._verify_integrity_sync(checkpoint_id):
            raise CheckpointIntegrityError("checkpoint failed integrity verification")

        resolved = self._resolve_checkpoint_relative_path(
            checkpoint_id,
            normalized,
        )

        if not resolved.is_file() or resolved.is_symlink():
            raise CheckpointIntegrityError("checkpoint backup must be a regular file")

        payload = resolved.read_bytes()

        if len(payload) != backup.size_bytes:
            raise CheckpointIntegrityError("checkpoint backup size does not match manifest")

        if sha256(payload).hexdigest() != backup.sha256:
            raise CheckpointIntegrityError("checkpoint backup hash does not match manifest")

        return payload

    async def cleanup(self, checkpoint_id: str) -> None:
        """Remove one checkpoint directory. Repeated cleanup is safe."""

        self._validate_identifier(checkpoint_id, "checkpoint_id")

        async with self._lock:
            await asyncio.to_thread(self._cleanup_sync, checkpoint_id)

    def _create_sync(self, request: ToolCallRequest) -> CheckpointRecord:
        checkpoint_id = f"checkpoint-{request.request_id}"
        self._validate_identifier(checkpoint_id, "checkpoint_id")

        target = self._resolve_target(request)
        target_path = self._path_resolver.to_relative(target)
        checkpoint_dir = self._checkpoint_dir(checkpoint_id)

        if checkpoint_dir.exists():
            existing = self._read_record_sync(checkpoint_id)
            self._validate_existing_record(existing, request, target_path)

            if not self._verify_integrity_sync(checkpoint_id):
                raise CheckpointIntegrityError("existing checkpoint failed integrity verification")

            return existing

        checkpoint_dir.mkdir(parents=False, exist_ok=False)

        try:
            backup = self._create_backup_record(
                checkpoint_dir,
                request.tool_name,
                target,
                target_path,
            )
            record = CheckpointRecord(
                checkpoint_id=checkpoint_id,
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                tool_name=request.tool_name,
                target_paths=(target_path,),
                created_at=datetime.now(UTC),
                status=CheckpointStatus.CREATED,
                backups=(backup,),
            )
            self._write_record_sync(record)
            return record
        except Exception:
            self._safe_remove_checkpoint_dir(checkpoint_dir)
            raise

    def _resolve_target(self, request: ToolCallRequest) -> Path:
        raw_path = request.arguments.get("path")

        if not isinstance(raw_path, str):
            raise ValueError(f"{request.tool_name} argument 'path' must be a string")

        if not raw_path or raw_path.isspace():
            raise ValueError(f"{request.tool_name} argument 'path' must not be empty")

        if request.tool_name == "write_file":
            return self._path_resolver.resolve_write_target(raw_path)

        if request.tool_name == "delete_file":
            return self._path_resolver.resolve_delete_target(raw_path)

        raise UnsupportedCheckpointToolError(
            f"tool does not support filesystem checkpoints: {request.tool_name}"
        )

    def _create_backup_record(
        self,
        checkpoint_dir: Path,
        tool_name: str,
        target: Path,
        target_path: str,
    ) -> BackupRecord:
        if not target.exists():
            if tool_name == "delete_file":
                raise CheckpointStoreError("delete target disappeared before checkpoint")

            return BackupRecord(
                target_path=target_path,
                existed=False,
                backup_path=None,
                sha256=None,
                size_bytes=None,
                mtime_ns=None,
                mode=None,
            )

        if target.is_symlink() or not target.is_file():
            raise CheckpointStoreError("checkpoint target must be a regular file")

        before = target.stat()
        payload = target.read_bytes()
        after = target.stat()

        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )

        if before_identity != after_identity or len(payload) != after.st_size:
            raise CheckpointStoreError("checkpoint source changed while being backed up")

        backup_relative = (Path(self.BACKUP_DIRECTORY) / "0001.bin").as_posix()
        backup_path = checkpoint_dir / Path(*PurePosixPath(backup_relative).parts)
        self._atomic_write_bytes(backup_path, payload)

        return BackupRecord(
            target_path=target_path,
            existed=True,
            backup_path=backup_relative,
            sha256=sha256(payload).hexdigest(),
            size_bytes=len(payload),
            mtime_ns=after.st_mtime_ns,
            mode=stat.S_IMODE(after.st_mode),
        )

    def _validate_existing_record(
        self,
        record: CheckpointRecord,
        request: ToolCallRequest,
        target_path: str,
    ) -> None:
        if (
            record.task_id != request.task_id
            or record.step_id != request.step_id
            or record.request_id != request.request_id
            or record.tool_name != request.tool_name
            or record.target_paths != (target_path,)
        ):
            raise CheckpointConflictError(
                "checkpoint_id already exists with different request semantics"
            )

    def _verify_integrity_sync(self, checkpoint_id: str) -> bool:
        try:
            record = self._read_record_sync(checkpoint_id)
            checkpoint_dir = self._checkpoint_dir(checkpoint_id)
            expected_files = {self.MANIFEST_NAME}

            if record.checkpoint_id != checkpoint_id:
                return False

            if len(record.target_paths) != len(record.backups):
                return False

            for backup in record.backups:
                if backup.target_path not in record.target_paths:
                    return False

                if backup.existed:
                    if (
                        backup.backup_path is None
                        or backup.sha256 is None
                        or backup.size_bytes is None
                        or backup.mtime_ns is None
                        or backup.mode is None
                    ):
                        return False

                    backup_path = self._resolve_checkpoint_relative_path(
                        checkpoint_id,
                        backup.backup_path,
                    )

                    if not backup_path.is_file() or backup_path.is_symlink():
                        return False

                    payload = backup_path.read_bytes()

                    if len(payload) != backup.size_bytes:
                        return False

                    if sha256(payload).hexdigest() != backup.sha256:
                        return False

                    expected_files.add(backup.backup_path)
                elif any(
                    value is not None
                    for value in (
                        backup.backup_path,
                        backup.sha256,
                        backup.size_bytes,
                        backup.mtime_ns,
                        backup.mode,
                    )
                ):
                    return False

            actual_files: set[str] = set()

            for child in checkpoint_dir.rglob("*"):
                if child.is_symlink():
                    return False

                if child.is_file():
                    actual_files.add(child.relative_to(checkpoint_dir).as_posix())

            return actual_files == expected_files
        except (
            OSError,
            CheckpointStoreError,
            ValueError,
            TypeError,
            KeyError,
        ):
            return False

    def _read_record_sync(self, checkpoint_id: str) -> CheckpointRecord:
        manifest_path = self._checkpoint_dir(checkpoint_id) / self.MANIFEST_NAME

        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise CheckpointNotFoundError(f"checkpoint not found: {checkpoint_id}")

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CheckpointIntegrityError("checkpoint manifest cannot be read") from error

        return self._record_from_dict(raw)

    def _write_record_sync(self, record: CheckpointRecord) -> None:
        manifest_path = self._checkpoint_dir(record.checkpoint_id) / self.MANIFEST_NAME
        payload = json.dumps(
            self._record_to_dict(record),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        self._atomic_write_bytes(manifest_path, payload + b"\n")

    def _record_to_dict(self, record: CheckpointRecord) -> dict[str, Any]:
        data = asdict(record)
        data["version"] = self.MANIFEST_VERSION
        data["target_paths"] = list(record.target_paths)
        data["backups"] = [asdict(backup) for backup in record.backups]
        data["created_at"] = record.created_at.isoformat()
        data["status"] = record.status.value
        return data

    def _record_from_dict(self, raw: object) -> CheckpointRecord:
        if not isinstance(raw, dict):
            raise CheckpointIntegrityError("checkpoint manifest must be a JSON object")

        if raw.get("version") != self.MANIFEST_VERSION:
            raise CheckpointIntegrityError("unsupported checkpoint manifest version")

        try:
            checkpoint_id = raw["checkpoint_id"]
            task_id = raw["task_id"]
            step_id = raw["step_id"]
            request_id = raw["request_id"]
            tool_name = raw["tool_name"]
            target_paths_raw = raw["target_paths"]
            created_at = datetime.fromisoformat(raw["created_at"])
            status_value = CheckpointStatus(raw["status"])
            backups_raw = raw["backups"]
        except (KeyError, TypeError, ValueError) as error:
            raise CheckpointIntegrityError("checkpoint manifest contains invalid fields") from error

        for field_name, value in (
            ("checkpoint_id", checkpoint_id),
            ("task_id", task_id),
            ("step_id", step_id),
            ("request_id", request_id),
            ("tool_name", tool_name),
        ):
            if not isinstance(value, str):
                raise CheckpointIntegrityError(f"manifest {field_name} must be a string")

        self._validate_identifier(checkpoint_id, "manifest checkpoint_id")

        if tool_name not in {"write_file", "delete_file"}:
            raise CheckpointIntegrityError("manifest tool_name is not checkpointable")

        if not isinstance(target_paths_raw, list) or not target_paths_raw:
            raise CheckpointIntegrityError("manifest target_paths must be a non-empty list")

        target_paths = tuple(
            self._validate_relative_file_path(value, "target_path") for value in target_paths_raw
        )

        if not isinstance(backups_raw, list) or len(backups_raw) != len(target_paths):
            raise CheckpointIntegrityError("manifest backups do not match target_paths")

        backups = tuple(self._backup_from_dict(value) for value in backups_raw)

        if created_at.tzinfo is None:
            raise CheckpointIntegrityError("manifest created_at must include a timezone")

        return CheckpointRecord(
            checkpoint_id=checkpoint_id,
            task_id=task_id,
            step_id=step_id,
            request_id=request_id,
            tool_name=tool_name,
            target_paths=target_paths,
            created_at=created_at.astimezone(UTC),
            status=status_value,
            backups=backups,
        )

    def _backup_from_dict(self, raw: object) -> BackupRecord:
        if not isinstance(raw, dict):
            raise CheckpointIntegrityError("backup metadata must be a JSON object")

        try:
            target_path = self._validate_relative_file_path(
                raw["target_path"],
                "backup target_path",
            )
            existed = raw["existed"]
            backup_path = raw["backup_path"]
            content_sha256 = raw["sha256"]
            size_bytes = raw["size_bytes"]
            mtime_ns = raw["mtime_ns"]
            mode = raw["mode"]
        except KeyError as error:
            raise CheckpointIntegrityError("backup metadata is missing fields") from error

        if not isinstance(existed, bool):
            raise CheckpointIntegrityError("backup existed must be a boolean")

        if backup_path is not None:
            backup_path = self._validate_relative_file_path(
                backup_path,
                "backup_path",
            )

        if content_sha256 is not None and (
            not isinstance(content_sha256, str)
            or len(content_sha256) != 64
            or any(char not in "0123456789abcdef" for char in content_sha256)
        ):
            raise CheckpointIntegrityError("backup sha256 is invalid")

        for field_name, value in (
            ("size_bytes", size_bytes),
            ("mtime_ns", mtime_ns),
            ("mode", mode),
        ):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise CheckpointIntegrityError(
                    f"backup {field_name} must be a non-negative integer or null"
                )

        return BackupRecord(
            target_path=target_path,
            existed=existed,
            backup_path=backup_path,
            sha256=content_sha256,
            size_bytes=size_bytes,
            mtime_ns=mtime_ns,
            mode=mode,
        )

    def _checkpoint_dir(self, checkpoint_id: str) -> Path:
        candidate = self._checkpoint_root / checkpoint_id
        self._ensure_within_checkpoint_root(candidate)
        return candidate

    def _resolve_checkpoint_relative_path(
        self,
        checkpoint_id: str,
        relative_path: str,
    ) -> Path:
        normalized = self._validate_relative_file_path(
            relative_path,
            "checkpoint relative path",
        )
        candidate = self._checkpoint_dir(checkpoint_id) / Path(*PurePosixPath(normalized).parts)
        resolved = candidate.resolve(strict=False)
        self._ensure_within_checkpoint_root(resolved)
        return resolved

    def _cleanup_sync(self, checkpoint_id: str) -> None:
        checkpoint_dir = self._checkpoint_dir(checkpoint_id)

        if not checkpoint_dir.exists() and not checkpoint_dir.is_symlink():
            return

        if checkpoint_dir.is_symlink():
            checkpoint_dir.unlink()
            return

        shutil.rmtree(checkpoint_dir)

    def _safe_remove_checkpoint_dir(self, checkpoint_dir: Path) -> None:
        try:
            if checkpoint_dir.is_symlink():
                checkpoint_dir.unlink()
            elif checkpoint_dir.exists():
                shutil.rmtree(checkpoint_dir)
        except OSError:
            pass

    def _atomic_write_bytes(self, target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = target.with_name(f"{target.name}.tmp")

        try:
            with temporary_path.open("wb") as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())

            os.replace(temporary_path, target)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _ensure_within_checkpoint_root(self, path: Path) -> None:
        root_text = os.path.normcase(os.path.abspath(self._checkpoint_root))
        path_text = os.path.normcase(os.path.abspath(path))

        try:
            common = os.path.commonpath((root_text, path_text))
        except ValueError as error:
            raise CheckpointIntegrityError("checkpoint path is on another drive") from error

        if common != root_text:
            raise CheckpointIntegrityError("checkpoint path escapes checkpoint root")

    @staticmethod
    def _validate_identifier(value: str, field_name: str) -> None:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string")

        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError(f"{field_name} contains unsafe characters")

    @staticmethod
    def _validate_relative_file_path(value: object, field_name: str) -> str:
        if not isinstance(value, str):
            raise CheckpointIntegrityError(f"{field_name} must be a string")

        if not value or value.isspace():
            raise CheckpointIntegrityError(f"{field_name} must not be empty")

        normalized = value.replace("\\", "/")
        posix_path = PurePosixPath(normalized)
        windows_path = PureWindowsPath(value)

        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.anchor)
            or ".." in posix_path.parts
            or ".." in windows_path.parts
        ):
            raise CheckpointIntegrityError(f"{field_name} must be relative")

        normalized_value = posix_path.as_posix()

        if normalized_value in {"", "."}:
            raise CheckpointIntegrityError(f"{field_name} must refer to a file")

        return normalized_value


class MockCheckpointManager:
    """No-side-effect checkpoint mock for orchestration tests."""

    async def create(self, request: ToolCallRequest) -> CheckpointResult:
        return CheckpointResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            checkpoint_id=f"checkpoint-{request.request_id}",
            status=ExecutionStatus.SUCCESS,
        )
