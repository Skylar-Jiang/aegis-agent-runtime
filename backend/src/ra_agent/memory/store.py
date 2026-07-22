from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from ra_agent.contracts import MemoryStatus, ToolCallRequest

from .models import MemoryRecord

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class MemoryStoreError(RuntimeError):
    """Base error raised by durable runtime-memory operations."""


class MemoryNotFoundError(MemoryStoreError):
    """Raised when a memory request record cannot be found."""


class MemoryConflictError(MemoryStoreError):
    """Raised when one request_id is reused with different memory semantics."""


class MemoryIntegrityError(MemoryStoreError):
    """Raised when persisted memory metadata or payload is invalid."""


class MemoryStateTransitionError(MemoryStoreError):
    """Raised when a memory lifecycle transition is not permitted."""


class MemoryValueError(MemoryStoreError):
    """Raised when a memory key or value cannot be safely persisted."""


class FilesystemMemoryStore:
    """Persist versioned memory writes and expose only trusted versions to readers."""

    MANIFEST_NAME = "manifest.json"
    PAYLOAD_NAME = "payload.json"
    RECORDS_DIRECTORY = "records"
    MANIFEST_VERSION = 1

    def __init__(
        self,
        memory_root: Path,
        *,
        max_value_bytes: int,
    ) -> None:
        if max_value_bytes <= 0:
            raise ValueError("max_value_bytes must be positive")

        if memory_root.exists() and memory_root.is_symlink():
            raise MemoryStoreError("memory root must not be a symbolic link")

        memory_root.mkdir(parents=True, exist_ok=True)
        resolved_root = memory_root.resolve(strict=True)

        if not resolved_root.is_dir():
            raise MemoryStoreError("memory root must be a directory")

        records_root = resolved_root / self.RECORDS_DIRECTORY
        if records_root.exists() and records_root.is_symlink():
            raise MemoryStoreError("memory records root must not be a symbolic link")
        records_root.mkdir(parents=False, exist_ok=True)

        if not records_root.is_dir():
            raise MemoryStoreError("memory records root must be a directory")

        self._memory_root = resolved_root
        self._records_root = records_root.resolve(strict=True)
        self._max_value_bytes = max_value_bytes
        self._lock = asyncio.Lock()

    @property
    def memory_root(self) -> Path:
        return self._memory_root

    @property
    def records_root(self) -> Path:
        return self._records_root

    @property
    def max_value_bytes(self) -> int:
        return self._max_value_bytes

    async def stage(
        self,
        request: ToolCallRequest,
        *,
        key: str,
        value: object,
    ) -> MemoryRecord:
        """Persist one untrusted memory version in PENDING state."""

        self._validate_identifier(request.request_id, "request_id")
        normalized_key = self._validate_key(key)
        payload = self._canonical_json_bytes(value)

        if len(payload) > self._max_value_bytes:
            raise MemoryValueError(
                "memory value exceeds configured limit: "
                f"{len(payload)} > {self._max_value_bytes} bytes"
            )

        async with self._lock:
            return await asyncio.to_thread(
                self._stage_sync,
                request,
                normalized_key,
                payload,
            )

    async def get(self, request_id: str) -> MemoryRecord:
        """Load one memory record by idempotency request_id."""

        self._validate_identifier(request_id, "request_id")
        async with self._lock:
            return await asyncio.to_thread(self._read_record_sync, request_id)

    async def get_trusted(self, key: str) -> MemoryRecord | None:
        """Return the latest trusted version for a key, never pending or rejected data."""

        normalized_key = self._validate_key(key)
        async with self._lock:
            return await asyncio.to_thread(self._get_trusted_sync, normalized_key)

    async def get_trusted_value(self, key: str) -> tuple[MemoryRecord, object] | None:
        """Return the latest trusted record together with its decoded JSON value."""

        normalized_key = self._validate_key(key)
        async with self._lock:
            record = await asyncio.to_thread(self._get_trusted_sync, normalized_key)
            if record is None:
                return None
            value = await asyncio.to_thread(self._read_value_for_record_sync, record)
            return record, value

    async def read_value(self, request_id: str) -> object:
        """Read a record payload for internal inspection after integrity verification."""

        self._validate_identifier(request_id, "request_id")
        async with self._lock:
            return await asyncio.to_thread(self._read_value_sync, request_id)

    async def list_pending(self) -> tuple[MemoryRecord, ...]:
        """List pending memory versions in stable creation order."""

        async with self._lock:
            return await asyncio.to_thread(self._list_pending_sync)

    async def verify_integrity(self, request_id: str) -> bool:
        """Verify manifest structure, safe paths, canonical JSON, size and digest."""

        self._validate_identifier(request_id, "request_id")
        async with self._lock:
            return await asyncio.to_thread(self._verify_integrity_sync, request_id)

    async def mark_trusted(self, request_id: str) -> MemoryRecord:
        """Atomically transition PENDING memory to TRUSTED."""

        self._validate_identifier(request_id, "request_id")
        async with self._lock:
            return await asyncio.to_thread(self._mark_trusted_sync, request_id)

    async def mark_rejected(self, request_id: str, *, reason: str) -> MemoryRecord:
        """Atomically transition PENDING memory to REJECTED."""

        self._validate_identifier(request_id, "request_id")
        normalized_reason = self._validate_reason(reason)
        async with self._lock:
            return await asyncio.to_thread(
                self._mark_rejected_sync,
                request_id,
                normalized_reason,
            )

    async def mark_rolled_back(self, request_id: str, *, reason: str) -> MemoryRecord:
        """Rollback a PENDING or recently TRUSTED memory version."""

        self._validate_identifier(request_id, "request_id")
        normalized_reason = self._validate_reason(reason)
        async with self._lock:
            return await asyncio.to_thread(
                self._mark_rolled_back_sync,
                request_id,
                normalized_reason,
            )

    async def cleanup(self, request_id: str) -> None:
        """Remove only interrupted temporary files while retaining lifecycle tombstones."""

        self._validate_identifier(request_id, "request_id")
        async with self._lock:
            await asyncio.to_thread(self._cleanup_sync, request_id)

    def _stage_sync(
        self,
        request: ToolCallRequest,
        key: str,
        payload: bytes,
    ) -> MemoryRecord:
        if request.tool_name != "memory_write":
            raise MemoryValueError("memory store stage requires memory_write")

        request_dir = self._request_dir(request.request_id)
        content_digest = sha256(payload).hexdigest()

        if request_dir.exists() or request_dir.is_symlink():
            if request_dir.is_symlink() or not request_dir.is_dir():
                raise MemoryIntegrityError("memory request path must be a regular directory")

            existing = self._read_record_sync(request.request_id)
            expected = (
                existing.task_id == request.task_id
                and existing.step_id == request.step_id
                and existing.request_id == request.request_id
                and existing.tool_name == request.tool_name
                and existing.key == key
                and existing.content_sha256 == content_digest
                and existing.size_bytes == len(payload)
            )

            if not expected:
                raise MemoryConflictError(
                    "request_id already exists with different memory semantics"
                )

            if not self._verify_integrity_sync(request.request_id):
                raise MemoryIntegrityError("existing memory record failed integrity verification")

            return existing

        request_dir.mkdir(parents=False, exist_ok=False)

        try:
            payload_relative = (
                Path(self.RECORDS_DIRECTORY) / request.request_id / self.PAYLOAD_NAME
            ).as_posix()
            self._atomic_write_bytes(request_dir / self.PAYLOAD_NAME, payload)

            now = datetime.now(UTC)
            record = MemoryRecord(
                memory_id=f"memory-{request.request_id}",
                key=key,
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                tool_name=request.tool_name,
                payload_path=payload_relative,
                content_sha256=content_digest,
                size_bytes=len(payload),
                status=MemoryStatus.PENDING,
                created_at=now,
                updated_at=now,
            )
            self._write_record_sync(record)
            return record
        except Exception:
            self._safe_remove_request_dir(request_dir)
            raise

    def _get_trusted_sync(self, key: str) -> MemoryRecord | None:
        candidates = [
            record
            for record in self._read_all_records_sync()
            if record.key == key and record.status is MemoryStatus.TRUSTED
        ]

        if not candidates:
            return None

        return max(
            candidates,
            key=lambda record: (
                record.trusted_at or record.updated_at,
                record.updated_at,
                record.created_at,
                record.memory_id,
            ),
        )

    def _list_pending_sync(self) -> tuple[MemoryRecord, ...]:
        pending = [
            record
            for record in self._read_all_records_sync()
            if record.status is MemoryStatus.PENDING
        ]
        return tuple(sorted(pending, key=lambda record: (record.created_at, record.request_id)))

    def _read_all_records_sync(self) -> tuple[MemoryRecord, ...]:
        records: list[MemoryRecord] = []

        for child in self._records_root.iterdir():
            if child.is_symlink() or not child.is_dir():
                raise MemoryIntegrityError("memory records root contains an unsafe entry")

            self._validate_identifier(child.name, "persisted request_id")
            if not self._verify_integrity_sync(child.name):
                raise MemoryIntegrityError(
                    f"memory record failed integrity verification: {child.name}"
                )
            records.append(self._read_record_sync(child.name))

        return tuple(records)

    def _mark_trusted_sync(self, request_id: str) -> MemoryRecord:
        record = self._read_record_sync(request_id)

        if record.status is MemoryStatus.TRUSTED:
            return record

        if record.status is not MemoryStatus.PENDING:
            raise MemoryStateTransitionError(
                f"cannot transition memory from {record.status.value} to TRUSTED"
            )

        if not self._verify_integrity_sync(request_id):
            raise MemoryIntegrityError("memory record failed integrity verification")

        now = datetime.now(UTC)
        updated = record.model_copy(
            update={
                "status": MemoryStatus.TRUSTED,
                "updated_at": now,
                "trusted_at": now,
                "rejected_reason": None,
                "rollback_reason": None,
            }
        )
        self._write_record_sync(updated)
        return updated

    def _mark_rejected_sync(self, request_id: str, reason: str) -> MemoryRecord:
        record = self._read_record_sync(request_id)

        if record.status is MemoryStatus.REJECTED:
            return record

        if record.status is not MemoryStatus.PENDING:
            raise MemoryStateTransitionError(
                f"cannot transition memory from {record.status.value} to REJECTED"
            )

        if not self._verify_integrity_sync(request_id):
            raise MemoryIntegrityError("memory record failed integrity verification")

        updated = record.model_copy(
            update={
                "status": MemoryStatus.REJECTED,
                "updated_at": datetime.now(UTC),
                "rejected_reason": reason,
                "rollback_reason": None,
            }
        )
        self._write_record_sync(updated)
        return updated

    def _mark_rolled_back_sync(self, request_id: str, reason: str) -> MemoryRecord:
        record = self._read_record_sync(request_id)

        if record.status is MemoryStatus.ROLLED_BACK:
            return record

        if record.status not in {MemoryStatus.PENDING, MemoryStatus.TRUSTED}:
            raise MemoryStateTransitionError(
                f"cannot transition memory from {record.status.value} to ROLLED_BACK"
            )

        if not self._verify_integrity_sync(request_id):
            raise MemoryIntegrityError("memory record failed integrity verification")

        updated = record.model_copy(
            update={
                "status": MemoryStatus.ROLLED_BACK,
                "updated_at": datetime.now(UTC),
                "rollback_reason": reason,
            }
        )
        self._write_record_sync(updated)
        return updated

    def _verify_integrity_sync(self, request_id: str) -> bool:
        try:
            record = self._read_record_sync(request_id)
            request_dir = self._request_dir(request_id)

            if request_dir.is_symlink() or not request_dir.is_dir():
                return False

            expected_payload_path = (
                Path(self.RECORDS_DIRECTORY) / request_id / self.PAYLOAD_NAME
            ).as_posix()
            if record.request_id != request_id:
                return False
            if record.memory_id != f"memory-{request_id}":
                return False
            if record.tool_name != "memory_write":
                return False
            if record.payload_path != expected_payload_path:
                return False

            payload_path = self._resolve_payload_path(record.payload_path)
            if payload_path.is_symlink() or not payload_path.is_file():
                return False

            payload = payload_path.read_bytes()
            if len(payload) != record.size_bytes:
                return False
            if sha256(payload).hexdigest() != record.content_sha256:
                return False

            decoded = self._decode_json_payload(payload)
            if self._canonical_json_bytes(decoded) != payload:
                return False

            actual_names = {child.name for child in request_dir.iterdir()}
            if actual_names != {self.MANIFEST_NAME, self.PAYLOAD_NAME}:
                return False

            return True
        except (
            OSError,
            MemoryStoreError,
            TypeError,
            ValueError,
            KeyError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            return False

    def _read_value_sync(self, request_id: str) -> object:
        if not self._verify_integrity_sync(request_id):
            raise MemoryIntegrityError("memory record failed integrity verification")
        return self._read_value_for_record_sync(self._read_record_sync(request_id))

    def _read_value_for_record_sync(self, record: MemoryRecord) -> object:
        payload_path = self._resolve_payload_path(record.payload_path)
        payload = payload_path.read_bytes()

        if len(payload) != record.size_bytes:
            raise MemoryIntegrityError("memory payload size does not match manifest")
        if sha256(payload).hexdigest() != record.content_sha256:
            raise MemoryIntegrityError("memory payload hash does not match manifest")

        decoded = self._decode_json_payload(payload)
        if self._canonical_json_bytes(decoded) != payload:
            raise MemoryIntegrityError("memory payload is not canonical JSON")
        return decoded

    def _read_record_sync(self, request_id: str) -> MemoryRecord:
        manifest_path = self._request_dir(request_id) / self.MANIFEST_NAME

        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise MemoryNotFoundError(f"memory record not found: {request_id}")

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MemoryIntegrityError("memory manifest cannot be read") from error

        return self._record_from_dict(raw)

    def _write_record_sync(self, record: MemoryRecord) -> None:
        manifest_path = self._request_dir(record.request_id) / self.MANIFEST_NAME
        payload = json.dumps(
            self._record_to_dict(record),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        self._atomic_write_bytes(manifest_path, payload + b"\n")

    def _record_to_dict(self, record: MemoryRecord) -> dict[str, Any]:
        return {
            "version": self.MANIFEST_VERSION,
            "memory_id": record.memory_id,
            "key": record.key,
            "task_id": record.task_id,
            "step_id": record.step_id,
            "request_id": record.request_id,
            "tool_name": record.tool_name,
            "payload_path": record.payload_path,
            "content_sha256": record.content_sha256,
            "size_bytes": record.size_bytes,
            "status": record.status.value,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "trusted_at": record.trusted_at.isoformat() if record.trusted_at else None,
            "rejected_reason": record.rejected_reason,
            "rollback_reason": record.rollback_reason,
        }

    def _record_from_dict(self, raw: object) -> MemoryRecord:
        if not isinstance(raw, dict):
            raise MemoryIntegrityError("memory manifest must be a JSON object")

        if raw.get("version") != self.MANIFEST_VERSION:
            raise MemoryIntegrityError("unsupported memory manifest version")

        try:
            memory_id = raw["memory_id"]
            key = raw["key"]
            task_id = raw["task_id"]
            step_id = raw["step_id"]
            request_id = raw["request_id"]
            tool_name = raw["tool_name"]
            payload_path = raw["payload_path"]
            content_sha256 = raw["content_sha256"]
            size_bytes = raw["size_bytes"]
            status = MemoryStatus(raw["status"])
            created_at = datetime.fromisoformat(raw["created_at"])
            updated_at = datetime.fromisoformat(raw["updated_at"])
            trusted_at_raw = raw["trusted_at"]
            rejected_reason = raw["rejected_reason"]
            rollback_reason = raw["rollback_reason"]
        except (KeyError, TypeError, ValueError) as error:
            raise MemoryIntegrityError("memory manifest contains invalid fields") from error

        for field_name, value in (
            ("memory_id", memory_id),
            ("task_id", task_id),
            ("step_id", step_id),
            ("request_id", request_id),
            ("tool_name", tool_name),
            ("payload_path", payload_path),
            ("content_sha256", content_sha256),
        ):
            if not isinstance(value, str) or not value or value.isspace():
                raise MemoryIntegrityError(f"memory manifest {field_name} must be a string")

        if not isinstance(key, str):
            raise MemoryIntegrityError("memory manifest key must be a string")
        normalized_key = self._validate_key(key)
        self._validate_identifier(request_id, "memory manifest request_id")
        self._validate_relative_path(payload_path, "memory manifest payload_path")

        if tool_name != "memory_write":
            raise MemoryIntegrityError("memory manifest tool_name must be memory_write")

        if not _SHA256_PATTERN.fullmatch(content_sha256):
            raise MemoryIntegrityError("memory manifest content_sha256 is invalid")

        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
            raise MemoryIntegrityError("memory manifest size_bytes is invalid")

        if created_at.tzinfo is None or updated_at.tzinfo is None:
            raise MemoryIntegrityError("memory timestamps must include a timezone")

        created_at = created_at.astimezone(UTC)
        updated_at = updated_at.astimezone(UTC)
        if updated_at < created_at:
            raise MemoryIntegrityError("memory updated_at precedes created_at")

        trusted_at: datetime | None = None
        if trusted_at_raw is not None:
            if not isinstance(trusted_at_raw, str):
                raise MemoryIntegrityError("memory trusted_at must be a string or null")
            try:
                trusted_at = datetime.fromisoformat(trusted_at_raw)
            except ValueError as error:
                raise MemoryIntegrityError("memory trusted_at is invalid") from error
            if trusted_at.tzinfo is None:
                raise MemoryIntegrityError("memory trusted_at must include a timezone")
            trusted_at = trusted_at.astimezone(UTC)

        if status is MemoryStatus.TRUSTED and trusted_at is None:
            raise MemoryIntegrityError("trusted memory must include trusted_at")
        if status in {MemoryStatus.PENDING, MemoryStatus.REJECTED} and trusted_at is not None:
            raise MemoryIntegrityError("untrusted memory must not include trusted_at")

        for field_name, value in (
            ("rejected_reason", rejected_reason),
            ("rollback_reason", rollback_reason),
        ):
            if value is not None and (not isinstance(value, str) or not value or value.isspace()):
                raise MemoryIntegrityError(f"memory {field_name} must be a string or null")

        return MemoryRecord(
            memory_id=memory_id,
            key=normalized_key,
            task_id=task_id,
            step_id=step_id,
            request_id=request_id,
            tool_name=tool_name,
            payload_path=payload_path,
            content_sha256=content_sha256,
            size_bytes=size_bytes,
            status=status,
            created_at=created_at,
            updated_at=updated_at,
            trusted_at=trusted_at,
            rejected_reason=rejected_reason,
            rollback_reason=rollback_reason,
        )

    def _request_dir(self, request_id: str) -> Path:
        candidate = self._records_root / request_id
        self._ensure_within_root(candidate, self._records_root, "memory request path")
        return candidate

    def _resolve_payload_path(self, payload_path: str) -> Path:
        normalized = self._validate_relative_path(payload_path, "memory payload_path")
        candidate = self._memory_root / Path(*PurePosixPath(normalized).parts)

        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            raise MemoryIntegrityError("memory payload does not exist") from error

        self._ensure_within_root(resolved, self._memory_root, "memory payload")
        return resolved

    def _cleanup_sync(self, request_id: str) -> None:
        request_dir = self._request_dir(request_id)
        if not request_dir.exists():
            return
        if request_dir.is_symlink() or not request_dir.is_dir():
            raise MemoryIntegrityError("memory request path must be a regular directory")

        prefixes = (f".{self.MANIFEST_NAME}.", f".{self.PAYLOAD_NAME}.")
        for child in request_dir.iterdir():
            if (
                child.is_file()
                and not child.is_symlink()
                and child.name.startswith(prefixes)
                and child.name.endswith(".tmp")
            ):
                child.unlink()

    @staticmethod
    def _canonical_json_bytes(value: object) -> bytes:
        try:
            serialized = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as error:
            raise MemoryValueError("memory value must be finite JSON-serializable data") from error
        return serialized.encode("utf-8")

    @staticmethod
    def _decode_json_payload(payload: bytes) -> object:
        def reject_constant(value: str) -> object:
            raise ValueError(f"non-finite JSON value is not allowed: {value}")

        return json.loads(payload.decode("utf-8"), parse_constant=reject_constant)

    @staticmethod
    def _validate_identifier(value: object, field_name: str) -> str:
        if not isinstance(value, str):
            raise MemoryValueError(f"{field_name} must be a string")
        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise MemoryValueError(f"{field_name} contains unsafe characters")
        return value

    @staticmethod
    def _validate_key(key: object) -> str:
        if not isinstance(key, str):
            raise MemoryValueError("memory key must be a string")
        if not key or key.isspace():
            raise MemoryValueError("memory key must not be empty")
        if key != key.strip():
            raise MemoryValueError("memory key must not have surrounding whitespace")
        if len(key) > 512:
            raise MemoryValueError("memory key exceeds configured length limit")
        if "\x00" in key or any(ord(character) < 32 for character in key):
            raise MemoryValueError("memory key contains control characters")
        return key

    @staticmethod
    def _validate_reason(reason: object) -> str:
        if not isinstance(reason, str) or not reason or reason.isspace():
            raise MemoryValueError("memory transition reason must be a non-empty string")
        return reason.strip()

    @staticmethod
    def _validate_relative_path(value: object, field_name: str) -> str:
        if not isinstance(value, str) or not value or value.isspace():
            raise MemoryIntegrityError(f"{field_name} must be a non-empty string")
        if "\\" in value or "\x00" in value:
            raise MemoryIntegrityError(f"{field_name} must be a POSIX relative path")

        posix_path = PurePosixPath(value)
        windows_path = PureWindowsPath(value)
        if (
            posix_path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.anchor)
            or ".." in posix_path.parts
            or value in {".", ".."}
            or posix_path.as_posix() != value
        ):
            raise MemoryIntegrityError(f"{field_name} must be a normalized relative path")
        return value

    @staticmethod
    def _ensure_within_root(path: Path, root: Path, label: str) -> None:
        try:
            common = os.path.commonpath((str(path), str(root)))
        except ValueError as error:
            raise MemoryIntegrityError(f"{label} escapes its trusted root") from error

        if common != str(root):
            raise MemoryIntegrityError(f"{label} escapes its trusted root")

    @staticmethod
    def _atomic_write_bytes(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)

            os.replace(temporary_path, path)
            FilesystemMemoryStore._fsync_directory(path.parent)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY
        try:
            descriptor = os.open(directory, flags)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    @staticmethod
    def _safe_remove_request_dir(request_dir: Path) -> None:
        try:
            if request_dir.is_symlink():
                request_dir.unlink()
            elif request_dir.exists():
                shutil.rmtree(request_dir)
        except OSError:
            pass
