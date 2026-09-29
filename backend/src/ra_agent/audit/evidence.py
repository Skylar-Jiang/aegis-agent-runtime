"""Immutable SM2-signed objects referenced by Core behavior events."""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from ra_agent.crypto import (
    Canonicalizer,
    CryptoError,
    DigestProvider,
    EnvelopeService,
    load_json,
    signed_object_digest,
)
from ra_agent.crypto.envelope import ALGORITHM, SCHEMA_VERSION
from ra_agent.execution._cancellation import complete_before_cancelling

from .integrity import SignedObject

MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    storage_version: int
    task_id: str
    object_digest: str
    object: SignedObject


class FileEvidenceRecorder:
    """Sign and persist immutable evidence outside Agent-controlled request paths.

    The directory, signing key and key identifier are deployment configuration.
    Callers supply only the task, object type and explicit JSON payload.
    """

    def __init__(
        self,
        directory: Path,
        envelopes: EnvelopeService,
        *,
        key_id: str,
    ) -> None:
        if not isinstance(key_id, str) or not 1 <= len(key_id) <= 128:
            raise CryptoError("KEY_INVALID")
        self.directory = Path(directory).resolve()
        self.envelopes = envelopes
        self.key_id = key_id

    async def record_object(
        self,
        *,
        task_id: str,
        object_type: str,
        payload: dict[str, Any],
    ) -> str:
        return await complete_before_cancelling(
            asyncio.to_thread(
                self._record_object,
                task_id,
                object_type,
                deepcopy(payload),
            )
        )

    async def list_task_objects(
        self,
        task_id: str,
        references: set[str],
    ) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list_task_objects, task_id, set(references))

    def _record_object(
        self,
        task_id: str,
        object_type: str,
        payload: dict[str, Any],
    ) -> str:
        self._validate_task_payload(task_id, payload)
        canonical = Canonicalizer()
        payload_bytes = canonical.canonicalize(payload)
        if len(payload_bytes) > MAX_EVIDENCE_BYTES:
            raise CryptoError("INPUT_TOO_LARGE", "signed evidence exceeds 16 MiB")
        metadata = {
            "object_type": object_type,
            "schema_version": SCHEMA_VERSION,
            "algorithm": ALGORITHM,
            "key_id": self.key_id,
            "payload_digest": DigestProvider().sm3(payload_bytes),
        }
        identity = DigestProvider().sm3(canonical.canonicalize({"task_id": task_id, **metadata}))
        # A database transaction coordinates every recorder/process using this
        # directory. Holding it through publication avoids signing the same object
        # twice in normal concurrent use. Files retain their original reference.
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            index_path = self.directory / "object-index.sqlite3"
            with closing(sqlite3.connect(index_path, timeout=30)) as db:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS signed_objects ("
                        "identity TEXT PRIMARY KEY, reference TEXT NOT NULL)"
                    )
                    found = db.execute(
                        "SELECT reference FROM signed_objects WHERE identity = ?", (identity,)
                    ).fetchone()
                    if found is not None:
                        reference = found[0]
                        signed = self._validated_object(task_id, reference)
                        if (
                            signed.envelope.model_dump(exclude={"signature"}) != metadata
                            or canonical.canonicalize(signed.payload) != payload_bytes
                        ):
                            raise CryptoError("OBJECT_DUPLICATE", "evidence index conflict")
                        return reference
                    reference = self._sign_and_save(task_id, object_type, payload)
                    db.execute(
                        "INSERT INTO signed_objects(identity, reference) VALUES (?, ?)",
                        (identity, reference),
                    )
                    return reference
        except (OSError, sqlite3.Error) as exc:
            raise CryptoError("CHECK_UNAVAILABLE", "evidence index unavailable") from exc

    def _sign_and_save(
        self,
        task_id: str,
        object_type: str,
        payload: dict[str, Any],
    ) -> str:
        envelope = self.envelopes.sign(
            payload,
            object_type=object_type,
            key_id=self.key_id,
        )
        signed = SignedObject(payload=payload, envelope=envelope)
        reference = signed_object_digest(signed.payload, signed.envelope)
        record = EvidenceRecord(
            storage_version=1,
            task_id=task_id,
            object_digest=reference,
            object=signed,
        )
        data = Canonicalizer().canonicalize(record.model_dump())
        if len(data) > MAX_EVIDENCE_BYTES:
            raise CryptoError("INPUT_TOO_LARGE", "signed evidence exceeds 16 MiB")
        self._save_immutable(self._path(reference), data)
        return reference

    def _list_task_objects(
        self,
        task_id: str,
        references: set[str],
    ) -> list[dict[str, Any]]:
        if not isinstance(task_id, str) or not 1 <= len(task_id) <= 128:
            raise CryptoError("TASK_MISMATCH")
        objects: list[dict[str, Any]] = []
        for reference in sorted(references):
            signed = self._validated_object(task_id, reference)
            objects.append(signed.model_dump())
        return objects

    def _validated_object(self, task_id: str, reference: str) -> SignedObject:
        record = self._read(reference)
        if record.task_id != task_id:
            raise CryptoError("TASK_MISMATCH", "signed object belongs to another task")
        signed = record.object
        self._validate_task_payload(task_id, signed.payload)
        actual = signed_object_digest(signed.payload, signed.envelope)
        if actual != reference or record.object_digest != reference:
            raise CryptoError("OBJECT_MISSING", "signed object digest mismatch")
        if not self.envelopes.verify(
            signed.payload,
            signed.envelope,
            object_type=signed.envelope.object_type,
        ):
            raise CryptoError("SIGNATURE_INVALID", "stored signed object is invalid")
        return signed

    @staticmethod
    def _validate_task_payload(task_id: str, payload: dict[str, Any]) -> None:
        if not isinstance(task_id, str) or not 1 <= len(task_id) <= 128:
            raise CryptoError("TASK_MISMATCH")
        if not isinstance(payload, dict):
            raise CryptoError("INPUT_INVALID", "signed evidence payload must be an object")
        bound_task = payload.get("task_id")
        if bound_task is not None and bound_task != task_id:
            raise CryptoError("TASK_MISMATCH", "payload task does not match evidence task")

    def _path(self, reference: str) -> Path:
        if not isinstance(reference, str) or _DIGEST.fullmatch(reference) is None:
            raise CryptoError("OBJECT_MISSING")
        return self.directory / f"{reference}.json"

    def _read(self, reference: str) -> EvidenceRecord:
        try:
            path = self._path(reference)
            with path.open("rb") as stream:
                data = stream.read(MAX_EVIDENCE_BYTES + 1)
            if len(data) > MAX_EVIDENCE_BYTES:
                raise CryptoError("INPUT_TOO_LARGE", "stored evidence exceeds 16 MiB")
            record = EvidenceRecord.model_validate(load_json(data))
            if record.storage_version != 1:
                raise CryptoError("BUNDLE_INVALID", "unsupported evidence storage version")
            return record
        except FileNotFoundError as exc:
            raise CryptoError("OBJECT_MISSING", reference) from exc
        except (OSError, ValidationError) as exc:
            raise CryptoError("CHECK_UNAVAILABLE", "cannot read signed evidence") from exc

    def _save_immutable(self, destination: Path, data: bytes) -> None:
        temporary: str | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".evidence-", dir=self.directory)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.read_bytes() != data:
                    raise CryptoError("OBJECT_DUPLICATE", "evidence digest conflict") from None
        except OSError as exc:
            raise CryptoError("CHECK_UNAVAILABLE", "evidence storage unavailable") from exc
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError as exc:
                    raise CryptoError(
                        "CHECK_UNAVAILABLE",
                        "evidence temp cleanup failed",
                    ) from exc
