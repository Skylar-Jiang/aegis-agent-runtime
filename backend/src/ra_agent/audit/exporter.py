"""Bridge persisted Core events to anchored, independently verifiable bundles."""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import Awaitable, Callable
from typing import Any, cast

from ra_agent.crypto import CryptoError, EnvelopeService, signed_object_digest
from ra_agent.execution._cancellation import complete_before_cancelling
from ra_agent.gateway.interfaces import EventStore, EvidenceRecorder

from .checkpoints import CheckpointStore, create_checkpoint
from .integrity import CheckpointRecord, HashChain


def _rebuild_chain(task_id: str, events: list[dict[str, Any]]) -> tuple[HashChain, set[str]]:
    chain = HashChain(task_id)
    references: set[str] = set()
    for event in events:
        chain.append_event(event)
        for field in ("object_digest", "result_digest"):
            reference = event[field]
            if reference is not None:
                references.add(reference)
    return chain, references


def _object_references(objects: list[dict[str, Any]]) -> set[str]:
    return {signed_object_digest(obj["payload"], obj["envelope"]) for obj in objects}


def _anchors_chain(
    record: CheckpointRecord,
    *,
    task_id: str,
    chain: HashChain,
) -> bool:
    checkpoint = record.checkpoint
    return (
        record.task_id == task_id
        and checkpoint.from_seq == 1
        and checkpoint.to_seq == len(chain.entries)
        and hmac.compare_digest(checkpoint.chain_head, chain.get_chain_head())
    )


def _anchored_prefix(
    record: CheckpointRecord,
    *,
    task_id: str,
    events: list[dict[str, Any]],
    full_chain: HashChain,
    full_references: set[str],
) -> tuple[HashChain, set[str]]:
    if record.task_id != task_id or record.checkpoint.to_seq > len(events):
        raise CryptoError("ANCHOR_CONFLICT", "checkpoint does not anchor this task history")
    if record.checkpoint.to_seq == len(events):
        chain, references = full_chain, full_references
    else:
        chain, references = _rebuild_chain(task_id, events[: record.checkpoint.to_seq])
    if not _anchors_chain(record, task_id=task_id, chain=chain):
        raise CryptoError("ANCHOR_CONFLICT", "checkpoint does not anchor this task history")
    return chain, references


class AuditExportService:
    """Create a signed full-prefix bundle from one trusted EventStore snapshot."""

    def __init__(
        self,
        *,
        event_store: EventStore,
        evidence_recorder: EvidenceRecorder,
        envelopes: EnvelopeService,
        checkpoints: CheckpointStore,
        key_id: str,
    ) -> None:
        self.event_store = event_store
        self.evidence_recorder = evidence_recorder
        self.envelopes = envelopes
        self.checkpoints = checkpoints
        self.key_id = key_id

    async def export_task(self, *, task_id: str, checkpoint_id: str) -> dict:
        snapshot = cast(
            Callable[[str], Awaitable[tuple[list[dict[str, Any]], str | None]]] | None,
            getattr(self.event_store, "get_task_snapshot", None),
        )
        # The optional concrete-store method captures both values under one lock or
        # database transaction. Protocol-only stores may race; retry a changed head.
        for _ in range(1 if callable(snapshot) else 3):
            if callable(snapshot):
                events, persisted_head = await snapshot(task_id)
            else:
                events = await self.event_store.list_task_events(task_id)
                persisted_head = await self.event_store.get_chain_head(task_id)
            if not events:
                raise CryptoError("EVENT_INVALID", "cannot checkpoint an empty task")
            if persisted_head is None:
                raise CryptoError("CHECK_UNAVAILABLE", "event store has no cryptographic chain")
            chain, references = await asyncio.to_thread(_rebuild_chain, task_id, events)
            if hmac.compare_digest(persisted_head, chain.get_chain_head()):
                break
        else:
            raise CryptoError("CHAIN_HEAD_MISMATCH", "stored chain differs from reconstruction")

        try:
            record = await asyncio.to_thread(self.checkpoints.get_trusted_checkpoint, checkpoint_id)
        except CryptoError as exc:
            if exc.code != "ANCHOR_NOT_FOUND":
                raise
            record = None
        if record is not None:
            chain, references = await asyncio.to_thread(
                _anchored_prefix,
                record,
                task_id=task_id,
                events=events,
                full_chain=chain,
                full_references=references,
            )
        objects = await self._referenced_objects(task_id, references)
        if record is None:
            record = await asyncio.to_thread(
                create_checkpoint,
                chain,
                self.envelopes,
                key_id=self.key_id,
                checkpoint_id=checkpoint_id,
            )
            # Reject an unsupported output before committing its trusted anchor.
            await asyncio.to_thread(chain.export_bundle, record, objects=objects)
            try:
                await complete_before_cancelling(
                    asyncio.to_thread(self.checkpoints.save_checkpoint, record)
                )
            except CryptoError as save_exc:
                if save_exc.code != "ANCHOR_CONFLICT":
                    raise
                record = await asyncio.to_thread(
                    self.checkpoints.get_trusted_checkpoint, checkpoint_id
                )
                chain, references = await asyncio.to_thread(
                    _anchored_prefix,
                    record,
                    task_id=task_id,
                    events=events,
                    full_chain=chain,
                    full_references=references,
                )
                objects = await self._referenced_objects(task_id, references)
        return await asyncio.to_thread(chain.export_bundle, record, objects=objects)

    async def _referenced_objects(
        self, task_id: str, references: set[str]
    ) -> list[dict[str, Any]]:
        objects = await self.evidence_recorder.list_task_objects(task_id, references)
        try:
            provided = await asyncio.to_thread(_object_references, objects)
        except (KeyError, TypeError) as exc:
            raise CryptoError(
                "BUNDLE_INVALID", "evidence recorder returned a malformed object"
            ) from exc
        if len(provided) != len(objects):
            raise CryptoError("OBJECT_DUPLICATE")
        if references - provided:
            raise CryptoError("OBJECT_MISSING")
        if provided - references:
            raise CryptoError("OBJECT_UNREFERENCED")
        return objects
