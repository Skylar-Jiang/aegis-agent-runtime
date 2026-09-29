"""Per-task, full-prefix audit chain. Separate from Runtime Base's event recorder."""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ra_agent.crypto import Canonicalizer, CryptoError, DigestProvider, SignedEnvelope
from ra_agent.crypto.envelope import HexDigest, Identifier

GENESIS_HASH = "0" * 64
CHAIN_DOMAIN = b"AEGIS-CORE:AUDIT-CHAIN:v1\x00"
MAX_AUDIT_BUNDLE_BYTES = 64 * 1024 * 1024
EVENT_FIELDS = {
    "event_id",
    "sequence",
    "task_id",
    "parent_event_id",
    "type",
    "actor",
    "source_ref",
    "object_digest",
    "state",
    "decision",
    "result_digest",
    "occurred_at",
}


def _bundle_chunks(bundle: dict[str, Any]) -> Iterator[bytes]:
    """Bound compact UTF-8 transport JSON without changing signed JCS bytes."""
    size = 0
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    try:
        for chunk in encoder.iterencode(bundle):
            encoded = chunk.encode("utf-8")
            size += len(encoded)
            if size > MAX_AUDIT_BUNDLE_BYTES:
                raise CryptoError("INPUT_TOO_LARGE", "audit bundle exceeds 64 MiB")
            yield encoded
    except CryptoError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise CryptoError("BUNDLE_INVALID", "bundle must be finite UTF-8 JSON") from exc


def ensure_bundle_size(bundle: dict[str, Any]) -> None:
    for _ in _bundle_chunks(bundle):
        pass


def encode_bundle(bundle: dict[str, Any]) -> bytes:
    """Encode a downloadable bundle under the shared export/verification limit."""
    return b"".join(_bundle_chunks(bundle))


class AuditCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    checkpoint_id: Identifier
    from_seq: Annotated[int, Field(ge=1)]
    to_seq: Annotated[int, Field(ge=1)]
    chain_head: HexDigest
    created_at: str
    signer: Identifier

    @field_validator("created_at")
    @classmethod
    def timestamp(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value):
            raise ValueError("UTC timestamp ending in Z required")
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value

    @model_validator(mode="after")
    def full_prefix(self) -> AuditCheckpoint:
        if self.from_seq != 1 or self.to_seq < self.from_seq:
            raise ValueError("Core v1 checkpoints cover a complete prefix starting at 1")
        return self


class CheckpointRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    task_id: Identifier
    checkpoint: AuditCheckpoint
    signed_checkpoint: SignedEnvelope

    def signed_payload(self) -> dict[str, Any]:
        return {"task_id": self.task_id, "checkpoint": self.checkpoint.model_dump()}


class ChainEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    event: dict[str, Any]
    prev_hash: HexDigest
    event_digest: HexDigest
    chain_hash: HexDigest


class SignedObject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    payload: dict[str, Any]
    envelope: SignedEnvelope


class AuditBundle(CheckpointRecord):
    schema_version: Literal["1.0"] = "1.0"
    entries: Annotated[list[ChainEntry], Field(min_length=1, max_length=100_000)]
    objects: Annotated[list[SignedObject], Field(max_length=100_000)] = Field(default_factory=list)


class HashChain:
    def __init__(self, task_id: str) -> None:
        if not isinstance(task_id, str) or not 1 <= len(task_id) <= 128:
            raise CryptoError("EVENT_INVALID", "invalid task_id")
        self.task_id = task_id
        self._entries: list[dict[str, Any]] = []
        self._event_ids: set[str] = set()
        self._canonical = Canonicalizer()
        self._digest = DigestProvider()

    @property
    def entries(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._entries)

    def get_chain_head(self) -> str:
        return self._entries[-1]["chain_hash"] if self._entries else GENESIS_HASH

    def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        snapshot = copy.deepcopy(event)
        if not isinstance(snapshot, dict) or not EVENT_FIELDS.issubset(snapshot):
            raise CryptoError("EVENT_INVALID", "missing BehaviorEvent fields")
        if type(snapshot["sequence"]) is not int or snapshot["sequence"] != len(self._entries) + 1:
            raise CryptoError("SEQUENCE_INVALID")
        if snapshot["task_id"] != self.task_id:
            raise CryptoError("TASK_MISMATCH")
        event_id = snapshot["event_id"]
        if not isinstance(event_id, str) or not event_id or event_id in self._event_ids:
            raise CryptoError("EVENT_ID_INVALID")
        parent = snapshot["parent_event_id"]
        if parent is not None and (not isinstance(parent, str) or parent not in self._event_ids):
            raise CryptoError("PARENT_EVENT_MISSING")
        for field in ("object_digest", "result_digest"):
            value = snapshot[field]
            if value is not None and (
                not isinstance(value, str)
                or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)
            ):
                raise CryptoError("EVENT_INVALID", f"invalid {field}")
        event_digest = self._digest.sm3(self._canonical.canonicalize(snapshot))
        previous = self.get_chain_head()
        chain_hash = self._digest.sm3(
            CHAIN_DOMAIN + bytes.fromhex(previous) + bytes.fromhex(event_digest),
        )
        entry = {
            "event": snapshot,
            "prev_hash": previous,
            "event_digest": event_digest,
            "chain_hash": chain_hash,
        }
        self._entries.append(entry)
        self._event_ids.add(event_id)
        return copy.deepcopy(entry)

    def export_bundle(
        self,
        record: CheckpointRecord | dict[str, Any],
        *,
        objects: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        anchor = (
            record
            if isinstance(record, CheckpointRecord)
            else CheckpointRecord.model_validate(record)
        )
        if anchor.task_id != self.task_id or anchor.checkpoint.to_seq != len(self._entries):
            raise CryptoError("CHECKPOINT_RANGE_INVALID")
        if anchor.checkpoint.chain_head != self.get_chain_head():
            raise CryptoError("CHAIN_HEAD_MISMATCH")
        bundle = AuditBundle.model_validate(
            {
                **anchor.model_dump(),
                "schema_version": "1.0",
                "entries": self.entries,
                "objects": copy.deepcopy(objects or []),
            }
        ).model_dump()
        ensure_bundle_size(bundle)
        return bundle
