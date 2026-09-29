import asyncio
import threading
from pathlib import Path

import pytest
from ra_agent.audit import HashChain
from ra_agent.contracts import (
    ContractPermissionRule,
    ContractService,
    EffectClass,
    GrantEffect,
    GrantScope,
    PermissionContext,
    PermissionGrant,
    TaskContractCreateRequest,
    ToolCallEnvelope,
)
from ra_agent.events import BehaviorEvent, JsonlEventStore
from ra_agent.gateway import ToolGateway
from ra_agent.permissions import PermissionResolver


async def _confirmed_contract(service: ContractService):
    created = await service.create_contract(
        TaskContractCreateRequest(
            session_id="session-integration",
            task_id="task-integration",
            user_id="user-integration",
            goals=["write an integration report"],
            allowed=[
                ContractPermissionRule(
                    tool="create_file",
                    action="create_file",
                    resource="reports/**",
                    effect="WRITE",
                )
            ],
            policy_version="policy:1",
            tool_manifest_digest="a" * 64,
        )
    )
    return await service.confirm_contract(
        created.contract.contract_id,
        version=created.contract.version,
        confirmed_by="user-integration",
    )


def _permissions() -> PermissionContext:
    def grant(source: str, *, subject: str = "*", skill: str = "*") -> PermissionGrant:
        return PermissionGrant(
            subject=subject,
            skill=skill,
            tool="create_file",
            action="create_file",
            resource="reports/**",
            effect=GrantEffect.ALLOW,
            scope=GrantScope.GLOBAL,
            source=source,
        )

    return PermissionContext(
        user_grants=[grant("user", subject="user-integration")],
        skill_grants=[grant("skill", skill="writer")],
        system_grants=[grant("system")],
    )


def _call(record) -> ToolCallEnvelope:
    return ToolCallEnvelope(
        request_id="request-integration",
        task_id=record.contract.task_id,
        session_id=record.contract.session_id,
        contract_ref=record.ref,
        skill_ref="writer",
        tool="create_file",
        action="create_file",
        canonical_args={"path": "reports/result.md", "content": "verified"},
        resource="reports/result.md",
        effect_class=EffectClass.WRITE,
    )


@pytest.mark.asyncio
async def test_gateway_events_fit_p2_store_and_rebuild_p3_chain(tmp_path: Path) -> None:
    contracts = ContractService()
    contract = await _confirmed_contract(contracts)
    log = tmp_path / "events.jsonl"
    store = JsonlEventStore(log, chain_factory=HashChain)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        event_store=store,
    )

    await gateway.evaluate(_call(contract), _permissions())
    await gateway.execute("request-integration")

    events = await store.list_task_events(contract.contract.task_id)
    assert len(events) == 6
    assert [event["type"] for event in events] == [
        "PERMISSION_EVALUATED",
        "GATEWAY_ALLOWED",
        "PERMISSION_EVALUATED",
        "GATEWAY_ALLOWED",
        "EXECUTION_STARTED",
        "EXECUTION_FINISHED",
    ]
    assert all(
        BehaviorEvent.model_validate(event).model_dump(mode="json") == event for event in events
    )
    assert all("result" not in event for event in events)

    rebuilt = HashChain(contract.contract.task_id)
    for event in events:
        rebuilt.append_event(event)
    expected_head = rebuilt.get_chain_head()
    assert await store.get_chain_head(contract.contract.task_id) == expected_head

    reopened = JsonlEventStore(log, chain_factory=HashChain)
    assert await reopened.get_chain_head(contract.contract.task_id) == expected_head
    assert await reopened.list_task_events(contract.contract.task_id) == events


@pytest.mark.asyncio
@pytest.mark.parametrize("writes,content_bytes", [(1, 8), (6, 1024**2)])
async def test_gateway_binds_events_to_real_sm2_signed_objects(
    tmp_path: Path, monkeypatch, writes: int, content_bytes: int,
) -> None:
    from ra_agent.audit import FileEvidenceRecorder
    from ra_agent.crypto import EnvelopeService, OpenSSLSignatureProvider, generate_sm2_key

    key = tmp_path / "gateway-key.pem"
    public = generate_sm2_key(key)
    provider = OpenSSLSignatureProvider(
        {"gateway-key": public},
        private_keys={"gateway-key": key},
    )
    signatures = []
    original_sign = provider.sign_sm2

    def counted_sign(*args, **kwargs):
        signatures.append(1)
        return original_sign(*args, **kwargs)

    monkeypatch.setattr(provider, "sign_sm2", counted_sign)
    envelopes = EnvelopeService(provider)
    evidence = FileEvidenceRecorder(
        tmp_path / "evidence",
        envelopes,
        key_id="gateway-key",
    )
    contracts = ContractService()
    contract = await _confirmed_contract(contracts)
    store = JsonlEventStore(tmp_path / "events.jsonl", chain_factory=HashChain)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        event_store=store,
        signature_provider=provider,
        evidence_recorder=evidence,
    )

    for i in range(writes):
        call = _call(contract).model_copy(update={
            "request_id": f"request-integration-{i}",
            "canonical_args": {"path": "reports/result.md", "content": "x" * content_bytes},
        })
        await gateway.evaluate(call, _permissions())
        await gateway.execute(call.request_id)

    events = await store.list_task_events(contract.contract.task_id)
    references = {
        value
        for event in events
        for value in (event["object_digest"], event["result_digest"])
        if value is not None
    }
    objects = await evidence.list_task_objects(contract.contract.task_id, references)
    # The initial decision, execution recheck and execution start reference the
    # same original envelope; equivalent decisions reuse their original proof too.
    assert len(references) == 3 * writes
    assert len(signatures) == 3 * writes
    assert len({event["object_digest"] for event in events}) == writes
    assert {obj["envelope"]["object_type"] for obj in objects} == {
        "ToolCallEnvelope",
        "GatewayDecision",
        "ToolResult",
    }
    assert all(
        envelopes.verify(
            obj["payload"],
            obj["envelope"],
            object_type=obj["envelope"]["object_type"],
        )
        for obj in objects
    )



class _RejectingEventStore:
    async def append_event(self, event):
        raise RuntimeError("audit unavailable")

    async def list_task_events(self, task_id):
        return []

    async def get_chain_head(self, task_id):
        return None


@pytest.mark.asyncio
async def test_gateway_does_not_cache_allow_when_audit_append_fails() -> None:
    from ra_agent.gateway import GatewayError

    contracts = ContractService()
    contract = await _confirmed_contract(contracts)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        event_store=_RejectingEventStore(),
    )

    with pytest.raises(RuntimeError, match="audit unavailable"):
        await gateway.evaluate(_call(contract), _permissions())
    with pytest.raises(GatewayError, match="evaluated before execute"):
        await gateway.execute("request-integration")


async def _run_real_evidence_gateway(tmp_path: Path):
    from ra_agent.audit import FileEvidenceRecorder
    from ra_agent.crypto import EnvelopeService, OpenSSLSignatureProvider, generate_sm2_key

    key = tmp_path / "export-key.pem"
    public = generate_sm2_key(key)
    provider = OpenSSLSignatureProvider(
        {"export-key": public},
        private_keys={"export-key": key},
    )
    envelopes = EnvelopeService(provider)
    evidence = FileEvidenceRecorder(
        tmp_path / "evidence",
        envelopes,
        key_id="export-key",
    )
    contracts = ContractService()
    contract = await _confirmed_contract(contracts)
    store = JsonlEventStore(tmp_path / "events.jsonl", chain_factory=HashChain)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        event_store=store,
        signature_provider=provider,
        evidence_recorder=evidence,
    )
    await gateway.evaluate(_call(contract), _permissions())
    await gateway.execute("request-integration")
    return contract, store, evidence, envelopes, public


@pytest.mark.asyncio
async def test_exported_gateway_evidence_verifies_offline(tmp_path: Path) -> None:
    import copy

    from ra_agent.audit import (
        AuditExportService,
        AuditVerifier,
        FileCheckpointStore,
    )
    from ra_agent.crypto import EnvelopeService, OpenSSLSignatureProvider

    contract, store, evidence, envelopes, public = await _run_real_evidence_gateway(tmp_path)
    checkpoints = FileCheckpointStore(tmp_path / "checkpoints", envelopes)
    exporter = AuditExportService(
        event_store=store,
        evidence_recorder=evidence,
        envelopes=envelopes,
        checkpoints=checkpoints,
        key_id="export-key",
    )

    bundle = await exporter.export_task(
        task_id=contract.contract.task_id,
        checkpoint_id="checkpoint-integration",
    )

    readonly = EnvelopeService(OpenSSLSignatureProvider({"export-key": public}))
    trusted = FileCheckpointStore(tmp_path / "checkpoints", readonly)
    verifier = AuditVerifier(readonly, trusted)
    result = verifier.verify_bundle(
        bundle,
        trusted_checkpoint_id="checkpoint-integration",
        task_id=contract.contract.task_id,
    )
    assert result.valid
    assert result.verified_events == 6
    assert len(bundle["objects"]) == 3

    tampered = copy.deepcopy(bundle)
    tampered["entries"][0]["event"]["decision"] = "DENY"
    failed = verifier.verify_bundle(
        tampered,
        trusted_checkpoint_id="checkpoint-integration",
        task_id=contract.contract.task_id,
    )
    assert not failed.valid
    assert failed.errors[0].code == "CHAIN_INVALID"


class _WrongHeadStore:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def append_event(self, event):
        return await self.wrapped.append_event(event)

    async def list_task_events(self, task_id):
        return await self.wrapped.list_task_events(task_id)

    async def get_chain_head(self, task_id):
        return "f" * 64


class _WrongSnapshotStore(_WrongHeadStore):
    async def get_task_snapshot(self, task_id):
        events, _ = await self.wrapped.get_task_snapshot(task_id)
        return events, "f" * 64


class _AppendAfterListStore:
    def __init__(self, wrapped):
        self.wrapped = wrapped
        self.appended = False

    async def append_event(self, event):
        return await self.wrapped.append_event(event)

    async def _append_once(self, events):
        if not self.appended:
            self.appended = True
            last = events[-1]
            await self.wrapped.append_event(
                last
                | {
                    "event_id": "event-after-snapshot",
                    "parent_event_id": last["event_id"],
                    "sequence": last["sequence"] + 1,
                    "object_digest": None,
                    "result_digest": None,
                    "evidence_refs": [],
                }
            )

    async def list_task_events(self, task_id):
        events = await self.wrapped.list_task_events(task_id)
        await self._append_once(events)
        return events

    async def get_chain_head(self, task_id):
        return await self.wrapped.get_chain_head(task_id)


class _AppendAfterSnapshotStore(_AppendAfterListStore):
    async def get_task_snapshot(self, task_id):
        snapshot = await self.wrapped.get_task_snapshot(task_id)
        await self._append_once(snapshot[0])
        return snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["jsonl", "sqlite", "fallback"])
async def test_export_anchors_coherent_prefix_when_event_appends_between_reads(
    tmp_path: Path, backend: str
) -> None:
    from ra_agent.audit import AuditExportService, AuditVerifier, FileCheckpointStore
    from ra_agent.crypto import EnvelopeService, OpenSSLSignatureProvider
    from ra_agent.events import SqliteEventStore

    contract, source, evidence, envelopes, public = await _run_real_evidence_gateway(tmp_path)
    store = source
    if backend == "sqlite":
        store = SqliteEventStore(tmp_path / "core.sqlite3")
        for row in await source.list_task_events(contract.contract.task_id):
            await store.append_event(row)
    interleaved = (
        _AppendAfterListStore(store)
        if backend == "fallback"
        else _AppendAfterSnapshotStore(store)
    )
    checkpoints = FileCheckpointStore(tmp_path / "checkpoints", envelopes)
    exporter = AuditExportService(
        event_store=interleaved,
        evidence_recorder=evidence,
        envelopes=envelopes,
        checkpoints=checkpoints,
        key_id="export-key",
    )
    bundle = await exporter.export_task(
        task_id=contract.contract.task_id,
        checkpoint_id=f"checkpoint-{backend}-growth",
    )
    assert interleaved.appended
    assert len(await store.list_task_events(contract.contract.task_id)) == 7
    assert len(bundle["entries"]) == (7 if backend == "fallback" else 6)
    readonly = EnvelopeService(OpenSSLSignatureProvider({"export-key": public}))
    verifier = AuditVerifier(readonly, FileCheckpointStore(tmp_path / "checkpoints", readonly))
    result = verifier.verify_bundle(
        bundle,
        trusted_checkpoint_id=f"checkpoint-{backend}-growth",
        task_id=contract.contract.task_id,
    )
    assert result.valid
    assert result.verified_events == len(bundle["entries"])


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [False, True])
async def test_export_rejects_event_store_chain_head_mismatch(
    tmp_path: Path, snapshot: bool
) -> None:
    from ra_agent.audit import AuditExportService, FileCheckpointStore
    from ra_agent.crypto import CryptoError

    contract, store, evidence, envelopes, _ = await _run_real_evidence_gateway(tmp_path)
    exporter = AuditExportService(
        event_store=_WrongSnapshotStore(store) if snapshot else _WrongHeadStore(store),
        evidence_recorder=evidence,
        envelopes=envelopes,
        checkpoints=FileCheckpointStore(tmp_path / "checkpoints", envelopes),
        key_id="export-key",
    )

    with pytest.raises(CryptoError) as mismatch:
        await exporter.export_task(
            task_id=contract.contract.task_id,
            checkpoint_id="checkpoint-mismatch",
        )
    assert mismatch.value.code == "CHAIN_HEAD_MISMATCH"


@pytest.mark.asyncio
async def test_export_rejects_missing_referenced_signed_object(tmp_path: Path) -> None:
    from ra_agent.audit import AuditExportService, FileCheckpointStore
    from ra_agent.crypto import CryptoError

    contract, store, evidence, envelopes, _ = await _run_real_evidence_gateway(tmp_path)
    events = await store.list_task_events(contract.contract.task_id)
    missing_reference = events[0]["object_digest"]
    (evidence.directory / f"{missing_reference}.json").unlink()
    exporter = AuditExportService(
        event_store=store,
        evidence_recorder=evidence,
        envelopes=envelopes,
        checkpoints=FileCheckpointStore(tmp_path / "checkpoints", envelopes),
        key_id="export-key",
    )

    with pytest.raises(CryptoError) as missing:
        await exporter.export_task(
            task_id=contract.contract.task_id,
            checkpoint_id="checkpoint-missing-object",
        )
    assert missing.value.code == "OBJECT_MISSING"


class _OmittingEvidenceRecorder:
    def __init__(self, wrapped):
        self.wrapped = wrapped

    async def record_object(self, **kwargs):
        return await self.wrapped.record_object(**kwargs)

    async def list_task_objects(self, task_id, references):
        return []


@pytest.mark.asyncio
async def test_export_rejects_evidence_recorder_that_omits_references(tmp_path: Path) -> None:
    from ra_agent.audit import AuditExportService, FileCheckpointStore
    from ra_agent.crypto import CryptoError

    contract, store, evidence, envelopes, _ = await _run_real_evidence_gateway(tmp_path)
    exporter = AuditExportService(
        event_store=store,
        evidence_recorder=_OmittingEvidenceRecorder(evidence),
        envelopes=envelopes,
        checkpoints=FileCheckpointStore(tmp_path / "checkpoints", envelopes),
        key_id="export-key",
    )

    with pytest.raises(CryptoError) as missing:
        await exporter.export_task(
            task_id=contract.contract.task_id,
            checkpoint_id="checkpoint-omitted-object",
        )
    assert missing.value.code == "OBJECT_MISSING"


@pytest.mark.asyncio
async def test_export_retry_reuses_same_checkpoint_idempotently(tmp_path: Path) -> None:
    from ra_agent.audit import AuditExportService, FileCheckpointStore

    contract, store, evidence, envelopes, _ = await _run_real_evidence_gateway(tmp_path)
    exporter = AuditExportService(
        event_store=store,
        evidence_recorder=evidence,
        envelopes=envelopes,
        checkpoints=FileCheckpointStore(tmp_path / "checkpoints", envelopes),
        key_id="export-key",
    )

    first = await exporter.export_task(
        task_id=contract.contract.task_id,
        checkpoint_id="checkpoint-retry",
    )
    second = await exporter.export_task(
        task_id=contract.contract.task_id,
        checkpoint_id="checkpoint-retry",
    )

    assert second == first


@pytest.mark.asyncio
async def test_export_retry_returns_original_verifiable_prefix_after_task_grows(tmp_path: Path):
    from ra_agent.audit import AuditExportService, AuditVerifier, FileCheckpointStore
    from ra_agent.crypto import EnvelopeService, OpenSSLSignatureProvider

    contract, store, evidence, envelopes, public = await _run_real_evidence_gateway(tmp_path)
    checkpoints = FileCheckpointStore(tmp_path / "checkpoints", envelopes)
    exporter = AuditExportService(
        event_store=store,
        evidence_recorder=evidence,
        envelopes=envelopes,
        checkpoints=checkpoints,
        key_id="export-key",
    )
    first = await exporter.export_task(
        task_id=contract.contract.task_id, checkpoint_id="checkpoint-growth-retry"
    )
    last = (await store.list_task_events(contract.contract.task_id))[-1]
    await store.append_event(
        last
        | {
            "event_id": "event-after-checkpoint",
            "parent_event_id": last["event_id"],
            "sequence": last["sequence"] + 1,
            "object_digest": None,
            "result_digest": None,
            "evidence_refs": [],
        }
    )

    second = await exporter.export_task(
        task_id=contract.contract.task_id, checkpoint_id="checkpoint-growth-retry"
    )
    assert second == first
    readonly = EnvelopeService(OpenSSLSignatureProvider({"export-key": public}))
    verified = AuditVerifier(
        readonly, FileCheckpointStore(tmp_path / "checkpoints", readonly)
    ).verify_bundle(
        second,
        trusted_checkpoint_id="checkpoint-growth-retry",
        task_id=contract.contract.task_id,
    )
    assert verified.valid and verified.verified_events == 6


@pytest.mark.asyncio
async def test_cancelled_export_waits_for_checkpoint_publication(tmp_path: Path):
    from ra_agent.audit import AuditExportService, FileCheckpointStore

    contract, store, evidence, envelopes, _ = await _run_real_evidence_gateway(tmp_path)
    checkpoints = FileCheckpointStore(tmp_path / "checkpoints", envelopes)
    original = checkpoints.save_checkpoint
    started = threading.Event()
    release = threading.Event()

    def paused_save(record):
        started.set()
        assert release.wait(5), "checkpoint worker did not resume"
        return original(record)

    checkpoints.save_checkpoint = paused_save
    exporter = AuditExportService(
        event_store=store,
        evidence_recorder=evidence,
        envelopes=envelopes,
        checkpoints=checkpoints,
        key_id="export-key",
    )
    operation = asyncio.create_task(
        exporter.export_task(
            task_id=contract.contract.task_id,
            checkpoint_id="checkpoint-cancelled-publication",
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done(), "export released while checkpoint worker was still active"
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert checkpoints.get_trusted_checkpoint("checkpoint-cancelled-publication")
    assert len(
        (
            await exporter.export_task(
                task_id=contract.contract.task_id,
                checkpoint_id="checkpoint-cancelled-publication",
            )
        )["entries"]
    ) == 6


@pytest.mark.asyncio
async def test_cancelled_evidence_record_waits_for_immutable_publication(tmp_path: Path):
    contract, _, evidence, _, _ = await _run_real_evidence_gateway(tmp_path)
    original = evidence._record_object
    started = threading.Event()
    release = threading.Event()
    payload = {"task_id": contract.contract.task_id, "result": "complete"}

    def paused_record(task_id, object_type, payload):
        started.set()
        assert release.wait(5), "evidence worker did not resume"
        return original(task_id, object_type, payload)

    evidence._record_object = paused_record
    operation = asyncio.create_task(
        evidence.record_object(
            task_id=contract.contract.task_id, object_type="ToolResult", payload=payload
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 5)
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done(), "recorder released while evidence worker was still active"
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    reference = await evidence.record_object(
        task_id=contract.contract.task_id, object_type="ToolResult", payload=payload
    )
    assert len(await evidence.list_task_objects(contract.contract.task_id, {reference})) == 1


@pytest.mark.asyncio
async def test_concurrent_export_reuses_same_checkpoint_idempotently(
    tmp_path: Path,
) -> None:
    from ra_agent.audit import AuditExportService, FileCheckpointStore

    contract, store, evidence, envelopes, _ = await _run_real_evidence_gateway(tmp_path)
    exporter = AuditExportService(
        event_store=store,
        evidence_recorder=evidence,
        envelopes=envelopes,
        checkpoints=FileCheckpointStore(tmp_path / "checkpoints", envelopes),
        key_id="export-key",
    )

    first, second = await asyncio.gather(
        exporter.export_task(
            task_id=contract.contract.task_id,
            checkpoint_id="checkpoint-concurrent-retry",
        ),
        exporter.export_task(
            task_id=contract.contract.task_id,
            checkpoint_id="checkpoint-concurrent-retry",
        ),
    )

    assert second == first
