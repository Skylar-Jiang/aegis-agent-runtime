"""Immutable external checkpoint files. Trust comes from independent deployment/ACLs."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from ra_agent.crypto import Canonicalizer, CryptoError, DigestProvider, EnvelopeService, load_json

from .integrity import AuditCheckpoint, CheckpointRecord, HashChain


class CheckpointStore(Protocol):
    def save_checkpoint(self, record: CheckpointRecord | dict[str, Any]) -> None: ...

    def get_trusted_checkpoint(self, checkpoint_id: str) -> CheckpointRecord: ...


def create_checkpoint(
    chain: HashChain, signer: EnvelopeService, *, key_id: str, checkpoint_id: str
) -> CheckpointRecord:
    checkpoint = AuditCheckpoint(
        checkpoint_id=checkpoint_id,
        from_seq=1,
        to_seq=len(chain.entries),
        chain_head=chain.get_chain_head(),
        created_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        signer=key_id,
    )
    payload = {"task_id": chain.task_id, "checkpoint": checkpoint.model_dump()}
    return CheckpointRecord(
        task_id=chain.task_id,
        checkpoint=checkpoint,
        signed_checkpoint=signer.sign(payload, object_type="AuditCheckpoint", key_id=key_id),
    )


def validate_checkpoint(record: CheckpointRecord, envelopes: EnvelopeService) -> None:
    if record.signed_checkpoint.key_id != record.checkpoint.signer or not envelopes.verify(
        record.signed_payload(),
        record.signed_checkpoint,
        object_type="AuditCheckpoint",
    ):
        raise CryptoError("SIGNATURE_INVALID", "invalid checkpoint signature or signer")


class FileCheckpointStore:
    """Call from a trusted writer process, not from Agent-controlled workspace code.

    IDs map to hashed filenames; writes are atomic, exclusive and idempotent.
    No 'latest' API: the caller pins the expected checkpoint ID out of band.
    """

    def __init__(self, directory: Path, envelopes: EnvelopeService) -> None:
        self.directory = Path(directory).resolve()
        self.envelopes = envelopes

    def _path(self, checkpoint_id: str) -> Path:
        if not isinstance(checkpoint_id, str) or not 1 <= len(checkpoint_id) <= 128:
            raise CryptoError("ANCHOR_NOT_FOUND")
        name = DigestProvider().sm3(checkpoint_id.encode("utf-8"))
        return self.directory / f"{name}.json"

    def save_checkpoint(self, record: CheckpointRecord | dict[str, Any]) -> None:
        try:
            checked = CheckpointRecord.model_validate(
                record.model_dump() if isinstance(record, CheckpointRecord) else record,
            )
        except ValidationError as exc:
            raise CryptoError("CHECKPOINT_INVALID") from exc
        validate_checkpoint(checked, self.envelopes)
        data = Canonicalizer().canonicalize(checked.model_dump())
        destination = self._path(checked.checkpoint.checkpoint_id)
        # Temp file in same filesystem, fsync then link: readers never see partial JSON,
        # and another writer cannot replace an existing anchor in a race.
        temporary: str | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".checkpoint-", dir=self.directory)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, destination)
            except FileExistsError:
                if destination.read_bytes() != data:
                    raise CryptoError("ANCHOR_CONFLICT") from None
        except OSError as exc:
            raise CryptoError("CHECK_UNAVAILABLE", "checkpoint storage unavailable") from exc
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError as exc:
                    raise CryptoError(
                        "CHECK_UNAVAILABLE", "checkpoint temp cleanup failed"
                    ) from exc

    def get_trusted_checkpoint(self, checkpoint_id: str) -> CheckpointRecord:
        path = self._path(checkpoint_id)
        try:
            if self.directory.exists() and not self.directory.is_dir():
                raise CryptoError("CHECK_UNAVAILABLE", "checkpoint directory unavailable")
            with path.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise CryptoError("CHECKPOINT_INVALID")
            record = CheckpointRecord.model_validate(load_json(raw))
        except FileNotFoundError as exc:
            raise CryptoError("ANCHOR_NOT_FOUND") from exc
        except OSError as exc:
            raise CryptoError("CHECK_UNAVAILABLE", "checkpoint storage unavailable") from exc
        except ValidationError as exc:
            raise CryptoError("CHECKPOINT_INVALID") from exc
        if record.checkpoint.checkpoint_id != checkpoint_id:
            raise CryptoError("ANCHOR_MISMATCH")
        validate_checkpoint(record, self.envelopes)
        return record
