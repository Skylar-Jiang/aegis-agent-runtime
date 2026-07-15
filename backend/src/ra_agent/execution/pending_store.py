from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class PendingStoreError(RuntimeError):
    """Base error for pending persistence failures."""


class PendingNotFoundError(PendingStoreError):
    """Raised when no pending record exists for a request."""


class PendingConflictError(PendingStoreError):
    """Raised when a request ID is reused with different semantics."""


class PendingIntegrityError(PendingStoreError):
    """Raised when pending content or metadata is corrupted."""


class PendingOperation(StrEnum):
    WRITE = "WRITE"
    DELETE = "DELETE"


class PendingStatus(StrEnum):
    PENDING = "PENDING"
    COMMITTED = "COMMITTED"


@dataclass(frozen=True, slots=True)
class PendingRecord:
    request_id: str
    checkpoint_id: str | None
    tool_name: str
    operation: PendingOperation
    target_path: str
    pending_path: str | None
    content_sha256: str | None
    size_bytes: int | None
    created_at: datetime
    status: PendingStatus


class PendingStore:
    """Filesystem-backed store for untrusted file changes."""

    MANIFEST_NAME = "manifest.json"
    PAYLOAD_NAME = "payload.bin"
    MANIFEST_VERSION = 1

    def __init__(self, pending_root: Path) -> None:
        self._pending_root = pending_root.resolve(strict=False)
        self._pending_root.mkdir(parents=True, exist_ok=True)

        if not self._pending_root.is_dir():
            raise PendingStoreError("pending root must be a directory")

        self._lock = asyncio.Lock()

    @property
    def pending_root(self) -> Path:
        return self._pending_root

    async def stage_write(
        self,
        request_id: str,
        target_path: str,
        content: bytes,
    ) -> PendingRecord:
        """Stage file content without modifying the trusted workspace."""

        self._validate_identifier(request_id, "request_id")
        normalized_target = self._validate_target_path(target_path)

        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")

        async with self._lock:
            return await asyncio.to_thread(
                self._stage_write_sync,
                request_id,
                normalized_target,
                content,
            )

    async def stage_delete(
        self,
        request_id: str,
        target_path: str,
    ) -> PendingRecord:
        """Stage a delete marker without deleting the trusted file."""

        self._validate_identifier(request_id, "request_id")
        normalized_target = self._validate_target_path(target_path)

        async with self._lock:
            return await asyncio.to_thread(
                self._stage_delete_sync,
                request_id,
                normalized_target,
            )

    async def bind_checkpoint(
        self,
        request_id: str,
        checkpoint_id: str,
    ) -> PendingRecord:
        """Bind the checkpoint created by Runtime to a staged change."""

        self._validate_identifier(request_id, "request_id")
        self._validate_identifier(checkpoint_id, "checkpoint_id")

        async with self._lock:
            return await asyncio.to_thread(
                self._bind_checkpoint_sync,
                request_id,
                checkpoint_id,
            )

    async def get(
        self,
        request_id: str,
    ) -> PendingRecord:
        """Load a pending record by request ID."""

        self._validate_identifier(request_id, "request_id")

        async with self._lock:
            return await asyncio.to_thread(
                self._read_record_sync,
                request_id,
            )

    async def verify_integrity(
        self,
        request_id: str,
    ) -> bool:
        """Verify manifest structure and staged payload integrity."""

        self._validate_identifier(request_id, "request_id")

        async with self._lock:
            return await asyncio.to_thread(
                self._verify_integrity_sync,
                request_id,
            )

    async def mark_committed(
        self,
        request_id: str,
    ) -> None:
        """Persist the COMMITTED state after CommitGate succeeds."""

        self._validate_identifier(request_id, "request_id")

        async with self._lock:
            await asyncio.to_thread(
                self._mark_committed_sync,
                request_id,
            )

    async def cleanup(
        self,
        request_id: str,
    ) -> None:
        """Remove pending data. Repeated cleanup is safe."""

        self._validate_identifier(request_id, "request_id")

        async with self._lock:
            await asyncio.to_thread(
                self._cleanup_sync,
                request_id,
            )

    def _stage_write_sync(
        self,
        request_id: str,
        target_path: str,
        content: bytes,
    ) -> PendingRecord:
        request_dir = self._request_dir(request_id)
        content_digest = sha256(content).hexdigest()

        if request_dir.exists():
            existing = self._read_record_sync(request_id)
            expected = (
                existing.operation is PendingOperation.WRITE
                and existing.tool_name == "write_file"
                and existing.target_path == target_path
                and existing.content_sha256 == content_digest
                and existing.size_bytes == len(content)
            )

            if not expected:
                raise PendingConflictError(
                    "request_id already exists with different write semantics"
                )

            if not self._verify_integrity_sync(request_id):
                raise PendingIntegrityError("existing staged write failed integrity verification")

            return existing

        request_dir.mkdir(parents=False, exist_ok=False)

        try:
            payload_path = request_dir / self.PAYLOAD_NAME
            self._atomic_write_bytes(payload_path, content)

            record = PendingRecord(
                request_id=request_id,
                checkpoint_id=None,
                tool_name="write_file",
                operation=PendingOperation.WRITE,
                target_path=target_path,
                pending_path=(Path(request_id) / self.PAYLOAD_NAME).as_posix(),
                content_sha256=content_digest,
                size_bytes=len(content),
                created_at=datetime.now(UTC),
                status=PendingStatus.PENDING,
            )

            self._write_record_sync(record)
            return record
        except Exception:
            self._safe_remove_request_dir(request_dir)
            raise

    def _stage_delete_sync(
        self,
        request_id: str,
        target_path: str,
    ) -> PendingRecord:
        request_dir = self._request_dir(request_id)

        if request_dir.exists():
            existing = self._read_record_sync(request_id)
            expected = (
                existing.operation is PendingOperation.DELETE
                and existing.tool_name == "delete_file"
                and existing.target_path == target_path
            )

            if not expected:
                raise PendingConflictError(
                    "request_id already exists with different delete semantics"
                )

            if not self._verify_integrity_sync(request_id):
                raise PendingIntegrityError("existing staged delete failed integrity verification")

            return existing

        request_dir.mkdir(parents=False, exist_ok=False)

        try:
            record = PendingRecord(
                request_id=request_id,
                checkpoint_id=None,
                tool_name="delete_file",
                operation=PendingOperation.DELETE,
                target_path=target_path,
                pending_path=None,
                content_sha256=None,
                size_bytes=None,
                created_at=datetime.now(UTC),
                status=PendingStatus.PENDING,
            )

            self._write_record_sync(record)
            return record
        except Exception:
            self._safe_remove_request_dir(request_dir)
            raise

    def _bind_checkpoint_sync(
        self,
        request_id: str,
        checkpoint_id: str,
    ) -> PendingRecord:
        record = self._read_record_sync(request_id)

        if record.status is not PendingStatus.PENDING:
            raise PendingConflictError("checkpoint cannot be bound to a non-pending record")

        if record.checkpoint_id == checkpoint_id:
            return record

        if record.checkpoint_id is not None:
            raise PendingConflictError("pending record is already bound to another checkpoint")

        updated = replace(
            record,
            checkpoint_id=checkpoint_id,
        )
        self._write_record_sync(updated)
        return updated

    def _mark_committed_sync(
        self,
        request_id: str,
    ) -> None:
        record = self._read_record_sync(request_id)

        if record.status is PendingStatus.COMMITTED:
            return

        if record.checkpoint_id is None:
            raise PendingConflictError("pending record must be bound to a checkpoint before commit")

        if not self._verify_integrity_sync(request_id):
            raise PendingIntegrityError("pending data failed integrity verification")

        updated = replace(
            record,
            status=PendingStatus.COMMITTED,
        )
        self._write_record_sync(updated)

    def _verify_integrity_sync(
        self,
        request_id: str,
    ) -> bool:
        try:
            record = self._read_record_sync(request_id)
            request_dir = self._request_dir(request_id)

            if record.request_id != request_id:
                return False

            if record.operation is PendingOperation.WRITE:
                if record.pending_path is None:
                    return False

                if record.content_sha256 is None:
                    return False

                if record.size_bytes is None:
                    return False

                payload_path = self._resolve_pending_path(record.pending_path)

                if payload_path.parent != request_dir:
                    return False

                if not payload_path.is_file():
                    return False

                payload = payload_path.read_bytes()

                if len(payload) != record.size_bytes:
                    return False

                if sha256(payload).hexdigest() != record.content_sha256:
                    return False

                expected_names = {
                    self.MANIFEST_NAME,
                    self.PAYLOAD_NAME,
                }
            elif record.operation is PendingOperation.DELETE:
                if record.pending_path is not None:
                    return False

                if record.content_sha256 is not None:
                    return False

                if record.size_bytes is not None:
                    return False

                expected_names = {
                    self.MANIFEST_NAME,
                }
            else:
                return False

            actual_names = {child.name for child in request_dir.iterdir()}

            return actual_names == expected_names
        except (
            OSError,
            PendingStoreError,
            ValueError,
            TypeError,
            KeyError,
        ):
            return False

    def _read_record_sync(
        self,
        request_id: str,
    ) -> PendingRecord:
        manifest_path = self._request_dir(request_id) / self.MANIFEST_NAME

        if not manifest_path.is_file():
            raise PendingNotFoundError(f"pending record not found: {request_id}")

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PendingIntegrityError("pending manifest cannot be read") from error

        return self._record_from_dict(raw)

    def _write_record_sync(
        self,
        record: PendingRecord,
    ) -> None:
        manifest_path = self._request_dir(record.request_id) / self.MANIFEST_NAME
        serialized = json.dumps(
            self._record_to_dict(record),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")

        self._atomic_write_bytes(
            manifest_path,
            serialized + b"\n",
        )

    def _record_to_dict(
        self,
        record: PendingRecord,
    ) -> dict[str, Any]:
        data = asdict(record)
        data["version"] = self.MANIFEST_VERSION
        data["operation"] = record.operation.value
        data["status"] = record.status.value
        data["created_at"] = record.created_at.isoformat()
        return data

    def _record_from_dict(
        self,
        raw: object,
    ) -> PendingRecord:
        if not isinstance(raw, dict):
            raise PendingIntegrityError("pending manifest must be a JSON object")

        if raw.get("version") != self.MANIFEST_VERSION:
            raise PendingIntegrityError("unsupported pending manifest version")

        try:
            request_id = raw["request_id"]
            checkpoint_id = raw["checkpoint_id"]
            tool_name = raw["tool_name"]
            operation = PendingOperation(raw["operation"])
            target_path = raw["target_path"]
            pending_path = raw["pending_path"]
            content_sha256 = raw["content_sha256"]
            size_bytes = raw["size_bytes"]
            created_at = datetime.fromisoformat(raw["created_at"])
            status = PendingStatus(raw["status"])
        except (
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise PendingIntegrityError("pending manifest contains invalid fields") from error

        if not isinstance(request_id, str):
            raise PendingIntegrityError("manifest request_id must be a string")

        self._validate_identifier(
            request_id,
            "manifest request_id",
        )

        if checkpoint_id is not None:
            if not isinstance(checkpoint_id, str):
                raise PendingIntegrityError("manifest checkpoint_id must be a string or null")

            self._validate_identifier(
                checkpoint_id,
                "manifest checkpoint_id",
            )

        if not isinstance(tool_name, str):
            raise PendingIntegrityError("manifest tool_name must be a string")

        if not isinstance(target_path, str):
            raise PendingIntegrityError("manifest target_path must be a string")

        normalized_target = self._validate_target_path(target_path)

        if pending_path is not None and not isinstance(
            pending_path,
            str,
        ):
            raise PendingIntegrityError("manifest pending_path must be a string or null")

        if content_sha256 is not None and not isinstance(
            content_sha256,
            str,
        ):
            raise PendingIntegrityError("manifest content_sha256 must be a string or null")

        if size_bytes is not None and (
            not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0
        ):
            raise PendingIntegrityError(
                "manifest size_bytes must be a non-negative integer or null"
            )

        if created_at.tzinfo is None:
            raise PendingIntegrityError("manifest created_at must include a timezone")

        return PendingRecord(
            request_id=request_id,
            checkpoint_id=checkpoint_id,
            tool_name=tool_name,
            operation=operation,
            target_path=normalized_target,
            pending_path=pending_path,
            content_sha256=content_sha256,
            size_bytes=size_bytes,
            created_at=created_at.astimezone(UTC),
            status=status,
        )

    def _request_dir(
        self,
        request_id: str,
    ) -> Path:
        candidate = self._pending_root / request_id
        self._ensure_within_pending_root(candidate)
        return candidate

    def _resolve_pending_path(
        self,
        relative_path: str,
    ) -> Path:
        posix_path = PurePosixPath(relative_path)
        windows_path = PureWindowsPath(relative_path)

        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.anchor)
            or ".." in posix_path.parts
            or ".." in windows_path.parts
        ):
            raise PendingIntegrityError("pending_path escapes the pending root")

        candidate = self._pending_root / Path(*posix_path.parts)
        resolved = candidate.resolve(strict=False)
        self._ensure_within_pending_root(resolved)
        return resolved

    def _cleanup_sync(
        self,
        request_id: str,
    ) -> None:
        request_dir = self._request_dir(request_id)

        if not request_dir.exists() and not request_dir.is_symlink():
            return

        if request_dir.is_symlink():
            request_dir.unlink()
            return

        shutil.rmtree(request_dir)

    def _safe_remove_request_dir(
        self,
        request_dir: Path,
    ) -> None:
        try:
            if request_dir.is_symlink():
                request_dir.unlink()
            elif request_dir.exists():
                shutil.rmtree(request_dir)
        except OSError:
            pass

    def _atomic_write_bytes(
        self,
        target: Path,
        content: bytes,
    ) -> None:
        target.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        temporary_name: str | None = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name

            os.replace(temporary_name, target)
        finally:
            if temporary_name is not None:
                temporary_path = Path(temporary_name)

                if temporary_path.exists():
                    temporary_path.unlink()

    def _ensure_within_pending_root(
        self,
        path: Path,
    ) -> None:
        root_text = os.path.normcase(os.path.abspath(self._pending_root))
        path_text = os.path.normcase(os.path.abspath(path))

        try:
            common = os.path.commonpath((root_text, path_text))
        except ValueError as error:
            raise PendingIntegrityError("pending path is on another drive") from error

        if common != root_text:
            raise PendingIntegrityError("pending path escapes pending root")

    @staticmethod
    def _validate_identifier(
        value: str,
        field_name: str,
    ) -> None:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string")

        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise ValueError(f"{field_name} contains unsafe characters")

    @staticmethod
    def _validate_target_path(
        target_path: str,
    ) -> str:
        if not isinstance(target_path, str):
            raise TypeError("target_path must be a string")

        if not target_path or target_path.isspace():
            raise ValueError("target_path must not be empty")

        posix_path = PurePosixPath(target_path)
        windows_path = PureWindowsPath(target_path)

        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.anchor)
            or ".." in posix_path.parts
            or ".." in windows_path.parts
        ):
            raise ValueError("target_path must be workspace-relative")

        normalized = PurePosixPath(target_path.replace("\\", "/")).as_posix()

        if normalized in {"", "."}:
            raise ValueError("target_path must refer to a file")

        return normalized
