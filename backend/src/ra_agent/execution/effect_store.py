from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from ra_agent.contracts import EffectRecord, EffectStatus

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class EffectStoreError(RuntimeError):
    """Base error raised by durable effect-fact persistence."""


class EffectNotFoundError(EffectStoreError):
    """Raised when no effect exists for an identifier."""


class EffectConflictError(EffectStoreError):
    """Raised when a request ID is reused with different effect semantics."""


class EffectIntegrityError(EffectStoreError):
    """Raised when an effect manifest is malformed or corrupted."""


class EffectStateTransitionError(EffectStoreError):
    """Raised when an effect status transition is not allowed."""


class FilesystemEffectStore:
    """Filesystem-backed source of truth for v0.4 ``EffectRecord`` facts."""

    MANIFEST_NAME = "effect.json"
    MANIFEST_VERSION = 1

    def __init__(self, effect_root: Path) -> None:
        if effect_root.exists() and effect_root.is_symlink():
            raise EffectStoreError("effect root must not be a symbolic link")
        self._effect_root = effect_root.resolve(strict=False)
        self._effect_root.mkdir(parents=True, exist_ok=True)
        if not self._effect_root.is_dir():
            raise EffectStoreError("effect root must be a directory")
        self._lock = asyncio.Lock()

    @property
    def effect_root(self) -> Path:
        return self._effect_root

    async def register(self, record: EffectRecord) -> EffectRecord:
        """Persist one effect, returning the current record for idempotent retries."""

        normalized = self._normalize_record(record)
        self._validate_identifier(normalized.request_id, "request_id")
        self._validate_identifier(normalized.effect_id, "effect_id")
        async with self._lock:
            return await asyncio.to_thread(self._register_sync, normalized)

    async def get(self, effect_id: str) -> EffectRecord:
        self._validate_identifier(effect_id, "effect_id")
        async with self._lock:
            records = await asyncio.to_thread(self._load_all_sync)
        for record in records:
            if record.effect_id == effect_id:
                return record
        raise EffectNotFoundError(f"effect not found: {effect_id}")

    async def get_by_request_id(self, request_id: str) -> EffectRecord | None:
        self._validate_identifier(request_id, "request_id")
        async with self._lock:
            try:
                return await asyncio.to_thread(self._read_record_sync, request_id)
            except EffectNotFoundError:
                return None

    async def list_by_task_id(self, task_id: str) -> tuple[EffectRecord, ...]:
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id must be a non-empty string")
        async with self._lock:
            records = await asyncio.to_thread(self._load_all_sync)
        matching = (item for item in records if item.task_id == task_id)
        return tuple(sorted(matching, key=self._sort_key))

    async def list_by_target(self, target_ref: str) -> tuple[EffectRecord, ...]:
        if not isinstance(target_ref, str) or not target_ref.strip():
            raise ValueError("target_ref must be a non-empty string")
        async with self._lock:
            records = await asyncio.to_thread(self._load_all_sync)
        return tuple(
            sorted((item for item in records if item.target_ref == target_ref), key=self._sort_key)
        )

    async def update_status(
        self,
        effect_id: str,
        *,
        expected_statuses: frozenset[EffectStatus],
        target_status: EffectStatus,
    ) -> EffectRecord:
        self._validate_identifier(effect_id, "effect_id")
        if not expected_statuses:
            raise ValueError("expected_statuses must not be empty")
        async with self._lock:
            return await asyncio.to_thread(
                self._update_status_sync,
                effect_id,
                expected_statuses,
                target_status,
            )

    async def verify_integrity(self, request_id: str) -> bool:
        self._validate_identifier(request_id, "request_id")
        async with self._lock:
            try:
                await asyncio.to_thread(self._read_record_sync, request_id)
            except EffectStoreError:
                return False
        return True

    def _register_sync(self, record: EffectRecord) -> EffectRecord:
        request_dir = self._request_dir(record.request_id)
        if request_dir.exists() or request_dir.is_symlink():
            existing = self._read_record_sync(record.request_id)
            if not self._same_semantics(existing, record):
                raise EffectConflictError(
                    "request_id already exists with different effect semantics"
                )
            return existing

        request_dir.mkdir(parents=False, exist_ok=False)
        try:
            self._write_record_sync(record)
        except Exception:
            self._remove_empty_request_dir(request_dir)
            raise
        return record

    def _update_status_sync(
        self,
        effect_id: str,
        expected_statuses: frozenset[EffectStatus],
        target_status: EffectStatus,
    ) -> EffectRecord:
        record = self._find_by_effect_id_sync(effect_id)
        if record.status is target_status:
            return record
        if record.status not in expected_statuses:
            expected = ", ".join(sorted(status.value for status in expected_statuses))
            raise EffectStateTransitionError(
                f"effect {effect_id} status {record.status.value} is not in {{{expected}}}"
            )
        updated = record.model_copy(update={"status": target_status})
        self._write_record_sync(updated)
        return updated

    def _find_by_effect_id_sync(self, effect_id: str) -> EffectRecord:
        for record in self._load_all_sync():
            if record.effect_id == effect_id:
                return record
        raise EffectNotFoundError(f"effect not found: {effect_id}")

    def _load_all_sync(self) -> tuple[EffectRecord, ...]:
        records: list[EffectRecord] = []
        for request_dir in sorted(self._effect_root.iterdir(), key=lambda item: item.name):
            if request_dir.is_symlink() or not request_dir.is_dir():
                raise EffectIntegrityError("effect root contains an unsafe entry")
            self._validate_identifier(request_dir.name, "request_id")
            records.append(self._read_record_sync(request_dir.name))
        return tuple(records)

    def _read_record_sync(self, request_id: str) -> EffectRecord:
        request_dir = self._request_dir(request_id)
        if request_dir.is_symlink():
            raise EffectIntegrityError("effect request directory must not be a symbolic link")
        if not request_dir.is_dir():
            raise EffectNotFoundError(f"effect request not found: {request_id}")
        entries = {entry.name for entry in request_dir.iterdir()}
        if entries != {self.MANIFEST_NAME}:
            raise EffectIntegrityError("effect request directory contains unexpected entries")
        manifest_path = request_dir / self.MANIFEST_NAME
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise EffectIntegrityError("effect manifest must be a regular file")
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise EffectIntegrityError("effect manifest cannot be read safely") from error
        if not isinstance(payload, dict) or payload.get("version") != self.MANIFEST_VERSION:
            raise EffectIntegrityError("effect manifest version is invalid")
        raw_record = payload.get("record")
        try:
            record = EffectRecord.model_validate(raw_record)
        except Exception as error:
            raise EffectIntegrityError("effect manifest record is invalid") from error
        record = self._normalize_record(record)
        if record.request_id != request_id:
            raise EffectIntegrityError("effect manifest request_id does not match directory")
        self._validate_identifier(record.effect_id, "effect_id")
        return record

    def _write_record_sync(self, record: EffectRecord) -> None:
        request_dir = self._request_dir(record.request_id)
        request_dir.mkdir(parents=True, exist_ok=True)
        if request_dir.is_symlink():
            raise EffectIntegrityError("effect request directory must not be a symbolic link")
        payload = {
            "version": self.MANIFEST_VERSION,
            "record": record.model_dump(mode="json"),
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        manifest_path = request_dir / self.MANIFEST_NAME
        fd, temporary_name = tempfile.mkstemp(prefix=".effect.", suffix=".tmp", dir=request_dir)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, manifest_path)
            self._fsync_directory(request_dir)
        except Exception:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            raise

    def _request_dir(self, request_id: str) -> Path:
        return self._effect_root / request_id

    @staticmethod
    def _normalize_record(record: EffectRecord) -> EffectRecord:
        refs = sorted(set(record.artifact_refs))
        return record.model_copy(update={"artifact_refs": refs})

    @staticmethod
    def _same_semantics(existing: EffectRecord, candidate: EffectRecord) -> bool:
        fields = (
            "effect_id",
            "task_id",
            "step_id",
            "request_id",
            "kind",
            "target_ref",
            "checkpoint_id",
            "artifact_refs",
        )
        return all(getattr(existing, field) == getattr(candidate, field) for field in fields)

    @staticmethod
    def _sort_key(record: EffectRecord) -> tuple[datetime, str]:
        return record.created_at, record.effect_id

    @staticmethod
    def _validate_identifier(value: str, label: str) -> None:
        if not isinstance(value, str) or _IDENTIFIER_PATTERN.fullmatch(value) is None:
            raise ValueError(f"{label} must match {_IDENTIFIER_PATTERN.pattern}")

    @staticmethod
    def _remove_empty_request_dir(request_dir: Path) -> None:
        try:
            request_dir.rmdir()
        except OSError:
            pass

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
