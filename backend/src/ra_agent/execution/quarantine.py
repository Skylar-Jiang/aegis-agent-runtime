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
from urllib.parse import urlsplit

from ra_agent.contracts import ToolCallRequest

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class QuarantineStoreError(RuntimeError):
    """Base error raised by durable download quarantine operations."""


class QuarantineNotFoundError(QuarantineStoreError):
    """Raised when a quarantine record cannot be found."""


class QuarantineConflictError(QuarantineStoreError):
    """Raised when request_id is reused with different download semantics."""


class QuarantineIntegrityError(QuarantineStoreError):
    """Raised when quarantine metadata or payload is invalid."""


class QuarantineStateTransitionError(QuarantineStoreError):
    """Raised when a quarantine lifecycle transition is not permitted."""


class QuarantineStatus(StrEnum):
    QUARANTINED = "QUARANTINED"
    COMMITTED = "COMMITTED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    request_id: str
    task_id: str
    step_id: str
    tool_name: str
    source_url: str
    final_url: str
    redirect_chain: tuple[str, ...]
    quarantine_path: str
    content_type: str
    content_sha256: str
    size_bytes: int
    http_status: int
    status: QuarantineStatus
    created_at: datetime
    updated_at: datetime
    rejected_reason: str | None = None
    rollback_reason: str | None = None


class FilesystemQuarantineStore:
    """Persist untrusted downloads outside the workspace until PostCheck decides."""

    MANIFEST_NAME = "manifest.json"
    PAYLOAD_NAME = "payload.bin"
    MANIFEST_VERSION = 1

    def __init__(self, quarantine_root: Path, *, max_download_bytes: int) -> None:
        if max_download_bytes <= 0:
            raise ValueError("max_download_bytes must be positive")
        if quarantine_root.exists() and quarantine_root.is_symlink():
            raise QuarantineStoreError("quarantine root must not be a symbolic link")

        quarantine_root.mkdir(parents=True, exist_ok=True)
        resolved_root = quarantine_root.resolve(strict=True)
        if not resolved_root.is_dir():
            raise QuarantineStoreError("quarantine root must be a directory")

        self._quarantine_root = resolved_root
        self._max_download_bytes = max_download_bytes
        self._lock = asyncio.Lock()

    @property
    def quarantine_root(self) -> Path:
        return self._quarantine_root

    @property
    def max_download_bytes(self) -> int:
        return self._max_download_bytes

    def create_temporary_path(self, request_id: str) -> Path:
        """Reserve an internal temporary path under the quarantine root."""

        self._validate_identifier(request_id, "request_id")
        descriptor, name = tempfile.mkstemp(
            dir=self._quarantine_root,
            prefix=f".{request_id}.",
            suffix=".download.tmp",
        )
        os.close(descriptor)
        return Path(name)

    async def stage(
        self,
        request: ToolCallRequest,
        *,
        source_url: str,
        final_url: str,
        redirect_chain: tuple[str, ...],
        temporary_path: Path,
        content_type: str,
        content_sha256: str,
        size_bytes: int,
        http_status: int,
    ) -> QuarantineRecord:
        """Atomically adopt one completed temporary download into quarantine."""

        self._validate_identifier(request.request_id, "request_id")
        self._validate_url(source_url, "source_url")
        self._validate_url(final_url, "final_url")
        normalized_chain = tuple(
            self._validate_url(url, "redirect_chain entry") for url in redirect_chain
        )
        normalized_content_type = self._validate_content_type(content_type)
        self._validate_sha256(content_sha256)
        self._validate_size(size_bytes)
        self._validate_http_status(http_status)

        if size_bytes > self._max_download_bytes:
            raise QuarantineIntegrityError(
                "download exceeds configured quarantine limit: "
                f"{size_bytes} > {self._max_download_bytes} bytes"
            )

        async with self._lock:
            return await asyncio.to_thread(
                self._stage_sync,
                request,
                source_url,
                final_url,
                normalized_chain,
                temporary_path,
                normalized_content_type,
                content_sha256,
                size_bytes,
                http_status,
            )

    async def get(self, request_id: str) -> QuarantineRecord:
        self._validate_identifier(request_id, "request_id")
        async with self._lock:
            return await asyncio.to_thread(self._read_record_sync, request_id)

    async def verify_integrity(self, request_id: str) -> bool:
        self._validate_identifier(request_id, "request_id")
        async with self._lock:
            return await asyncio.to_thread(self._verify_integrity_sync, request_id)

    async def payload_path(self, request_id: str) -> Path:
        """Return a verified internal payload path for PostCheck inspection."""

        self._validate_identifier(request_id, "request_id")
        async with self._lock:
            return await asyncio.to_thread(self._payload_path_sync, request_id)

    async def mark_committed(self, request_id: str) -> QuarantineRecord:
        self._validate_identifier(request_id, "request_id")
        async with self._lock:
            return await asyncio.to_thread(self._mark_committed_sync, request_id)

    async def mark_rejected(self, request_id: str, *, reason: str) -> QuarantineRecord:
        self._validate_identifier(request_id, "request_id")
        normalized_reason = self._validate_reason(reason)
        async with self._lock:
            return await asyncio.to_thread(
                self._mark_rejected_sync,
                request_id,
                normalized_reason,
            )

    async def mark_rolled_back(self, request_id: str, *, reason: str) -> QuarantineRecord:
        self._validate_identifier(request_id, "request_id")
        normalized_reason = self._validate_reason(reason)
        async with self._lock:
            return await asyncio.to_thread(
                self._mark_rolled_back_sync,
                request_id,
                normalized_reason,
            )

    async def cleanup(self, request_id: str) -> None:
        """Remove interrupted temporary files while retaining durable lifecycle evidence."""

        self._validate_identifier(request_id, "request_id")
        async with self._lock:
            await asyncio.to_thread(self._cleanup_sync, request_id)

    def _stage_sync(
        self,
        request: ToolCallRequest,
        source_url: str,
        final_url: str,
        redirect_chain: tuple[str, ...],
        temporary_path: Path,
        content_type: str,
        content_sha256: str,
        size_bytes: int,
        http_status: int,
    ) -> QuarantineRecord:
        if request.tool_name != "download_url":
            raise QuarantineIntegrityError("quarantine stage requires download_url")

        temporary = self._resolve_temporary_path(temporary_path)
        self._verify_temporary_payload(temporary, content_sha256, size_bytes)
        request_dir = self._request_dir(request.request_id)

        if request_dir.exists() or request_dir.is_symlink():
            try:
                if request_dir.is_symlink() or not request_dir.is_dir():
                    raise QuarantineIntegrityError(
                        "quarantine request path must be a regular directory"
                    )
                existing = self._read_record_sync(request.request_id)
                expected = (
                    existing.task_id == request.task_id
                    and existing.step_id == request.step_id
                    and existing.request_id == request.request_id
                    and existing.tool_name == request.tool_name
                    and existing.source_url == source_url
                    and existing.final_url == final_url
                    and existing.redirect_chain == redirect_chain
                    and existing.content_type == content_type
                    and existing.content_sha256 == content_sha256
                    and existing.size_bytes == size_bytes
                    and existing.http_status == http_status
                )
                if not expected:
                    raise QuarantineConflictError(
                        "request_id already exists with different download semantics"
                    )
                if not self._verify_integrity_sync(request.request_id):
                    raise QuarantineIntegrityError(
                        "existing quarantine record failed integrity verification"
                    )
                return existing
            finally:
                self._safe_unlink(temporary)

        request_dir.mkdir(parents=False, exist_ok=False)
        payload_path = request_dir / self.PAYLOAD_NAME
        try:
            os.replace(temporary, payload_path)
            self._fsync_directory(request_dir)
            now = datetime.now(UTC)
            record = QuarantineRecord(
                request_id=request.request_id,
                task_id=request.task_id,
                step_id=request.step_id,
                tool_name=request.tool_name,
                source_url=source_url,
                final_url=final_url,
                redirect_chain=redirect_chain,
                quarantine_path=(Path(request.request_id) / self.PAYLOAD_NAME).as_posix(),
                content_type=content_type,
                content_sha256=content_sha256,
                size_bytes=size_bytes,
                http_status=http_status,
                status=QuarantineStatus.QUARANTINED,
                created_at=now,
                updated_at=now,
            )
            self._write_record_sync(record)
            return record
        except Exception:
            self._safe_remove_request_dir(request_dir)
            self._safe_unlink(temporary)
            raise

    def _mark_committed_sync(self, request_id: str) -> QuarantineRecord:
        record = self._read_record_sync(request_id)
        if record.status is QuarantineStatus.COMMITTED:
            return record
        if record.status is not QuarantineStatus.QUARANTINED:
            raise QuarantineStateTransitionError(
                f"cannot commit quarantine from {record.status.value}"
            )
        if not self._verify_integrity_sync(request_id):
            raise QuarantineIntegrityError("quarantine failed integrity verification")
        updated = replace(
            record,
            status=QuarantineStatus.COMMITTED,
            updated_at=datetime.now(UTC),
        )
        self._write_record_sync(updated)
        return updated

    def _mark_rejected_sync(self, request_id: str, reason: str) -> QuarantineRecord:
        record = self._read_record_sync(request_id)
        if record.status is QuarantineStatus.REJECTED:
            return record
        if record.status is not QuarantineStatus.QUARANTINED:
            raise QuarantineStateTransitionError(
                f"cannot reject quarantine from {record.status.value}"
            )
        if not self._verify_integrity_sync(request_id):
            raise QuarantineIntegrityError("quarantine failed integrity verification")
        updated = replace(
            record,
            status=QuarantineStatus.REJECTED,
            rejected_reason=reason,
            updated_at=datetime.now(UTC),
        )
        self._write_record_sync(updated)
        return updated

    def _mark_rolled_back_sync(self, request_id: str, reason: str) -> QuarantineRecord:
        record = self._read_record_sync(request_id)
        if record.status is QuarantineStatus.ROLLED_BACK:
            return record
        if record.status not in {
            QuarantineStatus.QUARANTINED,
            QuarantineStatus.COMMITTED,
        }:
            raise QuarantineStateTransitionError(
                f"cannot rollback quarantine from {record.status.value}"
            )
        if not self._verify_integrity_sync(request_id):
            raise QuarantineIntegrityError("quarantine failed integrity verification")
        updated = replace(
            record,
            status=QuarantineStatus.ROLLED_BACK,
            rollback_reason=reason,
            updated_at=datetime.now(UTC),
        )
        self._write_record_sync(updated)
        return updated

    def _payload_path_sync(self, request_id: str) -> Path:
        record = self._read_record_sync(request_id)
        if not self._verify_integrity_sync(request_id):
            raise QuarantineIntegrityError("quarantine failed integrity verification")
        return self._resolve_relative_path(record.quarantine_path)

    def _verify_integrity_sync(self, request_id: str) -> bool:
        try:
            record = self._read_record_sync(request_id)
            request_dir = self._request_dir(request_id)
            if record.request_id != request_id:
                return False
            payload_path = self._resolve_relative_path(record.quarantine_path)
            if payload_path.parent != request_dir:
                return False
            if not payload_path.is_file() or payload_path.is_symlink():
                return False
            payload = payload_path.read_bytes()
            if len(payload) != record.size_bytes:
                return False
            if sha256(payload).hexdigest() != record.content_sha256:
                return False
            actual_names = {
                child.name for child in request_dir.iterdir() if not child.name.endswith(".tmp")
            }
            return actual_names == {self.MANIFEST_NAME, self.PAYLOAD_NAME}
        except (OSError, ValueError, TypeError, KeyError, QuarantineStoreError):
            return False

    def _read_record_sync(self, request_id: str) -> QuarantineRecord:
        manifest_path = self._request_dir(request_id) / self.MANIFEST_NAME
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise QuarantineNotFoundError(f"quarantine record not found: {request_id}")
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise QuarantineIntegrityError("quarantine manifest cannot be read") from error
        return self._record_from_dict(raw)

    def _write_record_sync(self, record: QuarantineRecord) -> None:
        payload = json.dumps(
            self._record_to_dict(record),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        self._atomic_write_bytes(
            self._request_dir(record.request_id) / self.MANIFEST_NAME,
            payload + b"\n",
        )

    def _record_to_dict(self, record: QuarantineRecord) -> dict[str, Any]:
        data = asdict(record)
        data["version"] = self.MANIFEST_VERSION
        data["redirect_chain"] = list(record.redirect_chain)
        data["status"] = record.status.value
        data["created_at"] = record.created_at.isoformat()
        data["updated_at"] = record.updated_at.isoformat()
        return data

    def _record_from_dict(self, raw: object) -> QuarantineRecord:
        if not isinstance(raw, dict):
            raise QuarantineIntegrityError("quarantine manifest must be a JSON object")
        if raw.get("version") != self.MANIFEST_VERSION:
            raise QuarantineIntegrityError("unsupported quarantine manifest version")
        try:
            request_id = raw["request_id"]
            task_id = raw["task_id"]
            step_id = raw["step_id"]
            tool_name = raw["tool_name"]
            source_url = raw["source_url"]
            final_url = raw["final_url"]
            redirect_chain_raw = raw["redirect_chain"]
            quarantine_path = raw["quarantine_path"]
            content_type = raw["content_type"]
            content_sha256 = raw["content_sha256"]
            size_bytes = raw["size_bytes"]
            http_status = raw["http_status"]
            status = QuarantineStatus(raw["status"])
            created_at = datetime.fromisoformat(raw["created_at"])
            updated_at = datetime.fromisoformat(raw["updated_at"])
            rejected_reason = raw.get("rejected_reason")
            rollback_reason = raw.get("rollback_reason")
        except (KeyError, TypeError, ValueError) as error:
            raise QuarantineIntegrityError("quarantine manifest contains invalid fields") from error

        for field_name, value in (
            ("request_id", request_id),
            ("task_id", task_id),
            ("step_id", step_id),
            ("tool_name", tool_name),
        ):
            if not isinstance(value, str):
                raise QuarantineIntegrityError(f"manifest {field_name} must be a string")

        self._validate_identifier(request_id, "manifest request_id")
        if tool_name != "download_url":
            raise QuarantineIntegrityError("manifest tool_name must be download_url")
        source_url = self._validate_url(source_url, "manifest source_url")
        final_url = self._validate_url(final_url, "manifest final_url")
        if not isinstance(redirect_chain_raw, list):
            raise QuarantineIntegrityError("manifest redirect_chain must be a list")
        redirect_chain = tuple(
            self._validate_url(value, "manifest redirect_chain entry")
            for value in redirect_chain_raw
        )
        quarantine_path = self._validate_relative_path(
            quarantine_path,
            "manifest quarantine_path",
        )
        content_type = self._validate_content_type(content_type)
        self._validate_sha256(content_sha256)
        self._validate_size(size_bytes)
        self._validate_http_status(http_status)
        for field_name, value in (
            ("rejected_reason", rejected_reason),
            ("rollback_reason", rollback_reason),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise QuarantineIntegrityError(f"manifest {field_name} is invalid")
        if created_at.tzinfo is None or updated_at.tzinfo is None:
            raise QuarantineIntegrityError("manifest timestamps must include timezone")

        return QuarantineRecord(
            request_id=request_id,
            task_id=task_id,
            step_id=step_id,
            tool_name=tool_name,
            source_url=source_url,
            final_url=final_url,
            redirect_chain=redirect_chain,
            quarantine_path=quarantine_path,
            content_type=content_type,
            content_sha256=content_sha256,
            size_bytes=size_bytes,
            http_status=http_status,
            status=status,
            created_at=created_at.astimezone(UTC),
            updated_at=updated_at.astimezone(UTC),
            rejected_reason=rejected_reason,
            rollback_reason=rollback_reason,
        )

    def _request_dir(self, request_id: str) -> Path:
        candidate = self._quarantine_root / request_id
        self._ensure_within_root(candidate)
        return candidate

    def _resolve_temporary_path(self, temporary_path: Path) -> Path:
        try:
            resolved = temporary_path.resolve(strict=True)
        except FileNotFoundError as error:
            raise QuarantineIntegrityError("download temporary payload does not exist") from error
        self._ensure_within_root(resolved)
        if resolved.parent != self._quarantine_root:
            raise QuarantineIntegrityError(
                "download temporary payload must be under quarantine root"
            )
        if not resolved.is_file() or resolved.is_symlink():
            raise QuarantineIntegrityError("download temporary payload must be a regular file")
        return resolved

    def _resolve_relative_path(self, relative_path: str) -> Path:
        normalized = self._validate_relative_path(relative_path, "quarantine_path")
        candidate = self._quarantine_root / Path(*PurePosixPath(normalized).parts)
        try:
            resolved = candidate.resolve(strict=True)
        except FileNotFoundError as error:
            raise QuarantineIntegrityError("quarantine payload does not exist") from error
        self._ensure_within_root(resolved)
        return resolved

    @staticmethod
    def _verify_temporary_payload(path: Path, expected_hash: str, expected_size: int) -> None:
        before = path.stat()
        payload = path.read_bytes()
        after = path.stat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise QuarantineIntegrityError("temporary download changed during verification")
        if len(payload) != expected_size:
            raise QuarantineIntegrityError("temporary download size does not match")
        if sha256(payload).hexdigest() != expected_hash:
            raise QuarantineIntegrityError("temporary download hash does not match")

    def _cleanup_sync(self, request_id: str) -> None:
        prefixes = (f".{request_id}.",)
        for child in self._quarantine_root.iterdir():
            if child.is_file() and not child.is_symlink() and child.name.startswith(prefixes):
                if child.name.endswith(".download.tmp"):
                    self._safe_unlink(child)
        request_dir = self._request_dir(request_id)
        if request_dir.is_dir() and not request_dir.is_symlink():
            for child in request_dir.iterdir():
                if child.is_file() and not child.is_symlink() and child.name.endswith(".tmp"):
                    self._safe_unlink(child)

    @staticmethod
    def _atomic_write_bytes(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
            FilesystemQuarantineStore._fsync_directory(path.parent)
        except Exception:
            FilesystemQuarantineStore._safe_unlink(temporary_path)
            raise

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
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
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass

    @staticmethod
    def _safe_remove_request_dir(path: Path) -> None:
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        except OSError:
            pass

    def _ensure_within_root(self, path: Path) -> None:
        root_text = os.path.normcase(str(self._quarantine_root))
        path_text = os.path.normcase(str(path.resolve(strict=False)))
        try:
            common = os.path.commonpath([root_text, path_text])
        except ValueError as error:
            raise QuarantineIntegrityError("path is outside quarantine root") from error
        if common != root_text:
            raise QuarantineIntegrityError("path is outside quarantine root")

    @staticmethod
    def _validate_identifier(value: object, field_name: str) -> str:
        if not isinstance(value, str):
            raise QuarantineIntegrityError(f"{field_name} must be a string")
        if not _IDENTIFIER_PATTERN.fullmatch(value):
            raise QuarantineIntegrityError(f"{field_name} contains unsafe characters")
        return value

    @staticmethod
    def _validate_relative_path(value: object, field_name: str) -> str:
        if not isinstance(value, str) or not value or value.isspace():
            raise QuarantineIntegrityError(f"{field_name} must be a non-empty string")
        if "\\" in value or "\x00" in value:
            raise QuarantineIntegrityError(f"{field_name} must be a POSIX relative path")
        posix = PurePosixPath(value)
        windows = PureWindowsPath(value)
        if (
            posix.is_absolute()
            or windows.is_absolute()
            or bool(windows.anchor)
            or ".." in posix.parts
            or value in {".", ".."}
            or posix.as_posix() != value
        ):
            raise QuarantineIntegrityError(f"{field_name} must be a normalized relative path")
        return value

    @staticmethod
    def _validate_url(value: object, field_name: str) -> str:
        if not isinstance(value, str) or not value or value.isspace():
            raise QuarantineIntegrityError(f"{field_name} must be a non-empty URL string")
        if any(ord(character) < 32 for character in value):
            raise QuarantineIntegrityError(f"{field_name} contains control characters")
        try:
            parsed = urlsplit(value)
            _ = parsed.port
        except ValueError as error:
            raise QuarantineIntegrityError(f"{field_name} is not a valid URL") from error
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise QuarantineIntegrityError(f"{field_name} must be an HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise QuarantineIntegrityError(f"{field_name} must not contain credentials")
        return value

    @staticmethod
    def _validate_content_type(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise QuarantineIntegrityError("content_type must be a non-empty string")
        if "\r" in value or "\n" in value:
            raise QuarantineIntegrityError("content_type contains line breaks")
        return value.strip()[:255]

    @staticmethod
    def _validate_sha256(value: object) -> str:
        if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
            raise QuarantineIntegrityError("content_sha256 must be lowercase SHA-256")
        return value

    @staticmethod
    def _validate_size(value: object) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise QuarantineIntegrityError("size_bytes must be a non-negative integer")
        return value

    @staticmethod
    def _validate_http_status(value: object) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or not 100 <= value <= 599:
            raise QuarantineIntegrityError("http_status must be an HTTP status code")
        return value

    @staticmethod
    def _validate_reason(value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            raise QuarantineIntegrityError("reason must be a non-empty string")
        return value.strip()
