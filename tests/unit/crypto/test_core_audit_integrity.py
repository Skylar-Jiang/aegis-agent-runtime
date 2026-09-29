import copy
import importlib.util
import tempfile
from concurrent.futures import ThreadPoolExecutor

import pytest

from ra_agent.crypto import signed_object_digest


def test_audit_module_exists():
    assert importlib.util.find_spec("ra_agent.audit") is not None


def event(sequence, task_id="task-1"):
    return {
        "event_id": f"event-{sequence}",
        "sequence": sequence,
        "task_id": task_id,
        "parent_event_id": f"event-{sequence - 1}" if sequence > 1 else None,
        "type": "TOOL_DECISION",
        "actor": "gateway",
        "source_ref": "request-1",
        "object_digest": None,
        "state": "EVALUATED",
        "decision": "ALLOW",
        "result_digest": None,
        "occurred_at": "2026-09-19T08:00:00Z",
        "execution_receipt_id": None,
    }


@pytest.fixture
def audit(tmp_path):
    from ra_agent.audit import (
        AuditVerifier,
        FileCheckpointStore,
        HashChain,
        create_checkpoint,
    )
    from ra_agent.crypto import (
        EnvelopeService,
        OpenSSLSignatureProvider,
        generate_sm2_key,
    )

    key = tmp_path / "key.pem"
    public = generate_sm2_key(key)
    signer = EnvelopeService(
        OpenSSLSignatureProvider({"audit-key": public}, private_keys={"audit-key": key})
    )
    readonly = EnvelopeService(OpenSSLSignatureProvider({"audit-key": public}))
    chain = HashChain("task-1")
    for sequence in range(1, 4):
        chain.append_event(event(sequence))
    record = create_checkpoint(chain, signer, key_id="audit-key", checkpoint_id="cp-1")
    store = FileCheckpointStore(tmp_path / "external-anchor", readonly)
    store.save_checkpoint(record)
    bundle = chain.export_bundle(record)
    return bundle, AuditVerifier(readonly, store), store, signer, chain


def test_valid_bundle_and_reopened_external_store(audit):
    from ra_agent.audit import AuditVerifier, FileCheckpointStore

    bundle, verifier, store, signer, _ = audit
    result = verifier.verify_bundle(
        bundle, trusted_checkpoint_id="cp-1", task_id="task-1"
    )
    assert result.valid and result.verified_events == 3
    assert result.anchored_to_seq == 3
    assert (
        result.tail_complete is False
    )  # No claim that an unseen newer tail never existed.
    reopened = FileCheckpointStore(store.directory, signer)
    assert (
        AuditVerifier(signer, reopened)
        .verify_bundle(
            bundle,
            trusted_checkpoint_id="cp-1",
            task_id="task-1",
        )
        .valid
    )


@pytest.mark.parametrize(
    "attack",
    [
        "edit",
        "delete",
        "reorder",
        "duplicate",
        "tail",
        "task",
        "parent",
        "prev_hash",
        "chain_hash",
        "extension",
    ],
)
def test_bundle_tamper_detection(audit, attack):
    bundle, verifier, _, _, _ = audit
    bad = copy.deepcopy(bundle)
    if attack == "edit":
        bad["entries"][0]["event"]["decision"] = "DENY"
    elif attack == "delete":
        del bad["entries"][1]
    elif attack == "reorder":
        bad["entries"][0], bad["entries"][1] = bad["entries"][1], bad["entries"][0]
    elif attack == "duplicate":
        bad["entries"][1] = copy.deepcopy(bad["entries"][0])
    elif attack == "tail":
        bad["entries"].pop()
    elif attack == "task":
        bad["task_id"] = "other-task"
    elif attack == "parent":
        bad["entries"][1]["event"]["parent_event_id"] = "absent"
    elif attack == "extension":
        bad["entries"][0]["event"]["execution_receipt_id"] = "changed"
    else:
        bad["entries"][0][attack] = "f" * 64
    result = verifier.verify_bundle(bad, trusted_checkpoint_id="cp-1", task_id="task-1")
    assert not result.valid and result.errors


def test_rehashed_replacement_history_fails_external_anchor(audit):
    from ra_agent.audit import HashChain, create_checkpoint

    _, verifier, _, signer, _ = audit
    replacement = HashChain("task-1")
    for i in range(1, 4):
        replacement.append_event(event(i) | {"decision": "DENY"})
    # Even possession of the signing key cannot silently overwrite the pinned checkpoint.
    forged = create_checkpoint(
        replacement, signer, key_id="audit-key", checkpoint_id="cp-1"
    )
    result = verifier.verify_bundle(
        replacement.export_bundle(forged),
        trusted_checkpoint_id="cp-1",
        task_id="task-1",
    )
    assert not result.valid
    assert result.errors[0].code == "ANCHOR_MISMATCH"


def test_no_external_anchor_cannot_claim_success(audit):
    bundle, verifier, _, _, _ = audit
    result = verifier.verify_bundle(
        bundle, trusted_checkpoint_id="missing", task_id="task-1"
    )
    assert not result.valid and result.errors[0].code == "ANCHOR_NOT_FOUND"


def test_checkpoints_are_immutable_and_safe_under_concurrent_writes(audit):
    from ra_agent.crypto import CryptoError

    bundle, _, store, _, _ = audit
    record = {k: bundle[k] for k in ("task_id", "checkpoint", "signed_checkpoint")}
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: store.save_checkpoint(record), range(8)))
    conflict = copy.deepcopy(record)
    conflict["checkpoint"]["chain_head"] = "0" * 64
    with pytest.raises(CryptoError):
        store.save_checkpoint(conflict)


def test_checkpoint_filename_does_not_follow_untrusted_identifier(audit):
    from ra_agent.crypto import CryptoError

    _, _, store, _, _ = audit
    with pytest.raises(CryptoError, match="ANCHOR_NOT_FOUND"):
        store.get_trusted_checkpoint("../../escape")


def test_chain_checks_sequence_task_parent_and_event_identity():
    from ra_agent.audit import HashChain
    from ra_agent.crypto import CryptoError

    for bad in [
        event(2),
        event(1, "other"),
        event(1) | {"sequence": True},
        event(1) | {"parent_event_id": "unknown"},
    ]:
        with pytest.raises(CryptoError):
            HashChain("task-1").append_event(bad)
    chain = HashChain("task-1")
    source = event(1)
    returned = chain.append_event(source)
    source["decision"] = "DENY"
    returned["event"]["decision"] = "DENY"
    assert chain.entries[0]["event"]["decision"] == "ALLOW"
    with pytest.raises(CryptoError):
        chain.append_event(event(2) | {"event_id": "event-1"})


def test_signed_objects_must_be_referenced_by_events(audit):
    from ra_agent.audit import HashChain, create_checkpoint

    _, verifier, store, signer, _ = audit
    payload = {"contract_id": "c-1", "task_id": "task-1", "version": 1}
    envelope = signer.sign(payload, object_type="TaskContractV2", key_id="audit-key")
    chain = HashChain("task-1")
    chain.append_event(
        event(1) | {"object_digest": signed_object_digest(payload, envelope)}
    )
    record = create_checkpoint(
        chain, signer, key_id="audit-key", checkpoint_id="objects"
    )
    store.save_checkpoint(record)
    bundle = chain.export_bundle(
        record, objects=[{"payload": payload, "envelope": envelope.model_dump()}]
    )
    assert verifier.verify_bundle(
        bundle, trusted_checkpoint_id="objects", task_id="task-1"
    ).valid
    bundle["objects"][0]["payload"]["version"] = 2
    assert not verifier.verify_bundle(
        bundle, trusted_checkpoint_id="objects", task_id="task-1"
    ).valid


def test_missing_referenced_signed_object_fails(audit):
    from ra_agent.audit import HashChain, create_checkpoint

    _, verifier, store, signer, _ = audit
    chain = HashChain("task-1")
    chain.append_event(event(1) | {"object_digest": "f" * 64})
    record = create_checkpoint(
        chain, signer, key_id="audit-key", checkpoint_id="missing-object"
    )
    store.save_checkpoint(record)
    result = verifier.verify_bundle(
        chain.export_bundle(record),
        trusted_checkpoint_id="missing-object",
        task_id="task-1",
    )
    assert not result.valid and result.errors[0].code == "OBJECT_MISSING"


def test_reference_cannot_swap_preexisting_signature_object_type(audit):
    from ra_agent.audit import HashChain, create_checkpoint

    _, verifier, store, signer, _ = audit
    payload = {"task_id": "task-1", "decision": "ALLOW"}
    first = signer.sign(payload, object_type="GatewayDecision", key_id="audit-key")
    second = signer.sign(payload, object_type="ToolResult", key_id="audit-key")
    chain = HashChain("task-1")
    chain.append_event(
        event(1) | {"object_digest": signed_object_digest(payload, first)}
    )
    record = create_checkpoint(
        chain, signer, key_id="audit-key", checkpoint_id="typed-ref"
    )
    store.save_checkpoint(record)
    bundle = chain.export_bundle(
        record, objects=[{"payload": payload, "envelope": first.model_dump()}]
    )
    assert verifier.verify_bundle(
        bundle, trusted_checkpoint_id="typed-ref", task_id="task-1"
    ).valid
    bundle["objects"][0]["envelope"] = second.model_dump()
    assert not verifier.verify_bundle(
        bundle, trusted_checkpoint_id="typed-ref", task_id="task-1"
    ).valid


def test_verifier_reports_temporary_storage_failure(audit, monkeypatch):
    bundle, verifier, _, _, _ = audit

    def unavailable(*args, **kwargs):
        raise PermissionError("simulated unavailable temporary directory")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", unavailable)
    result = verifier.verify_bundle(
        bundle, trusted_checkpoint_id="cp-1", task_id="task-1"
    )
    assert not result.valid and result.errors[0].code == "CHECK_UNAVAILABLE"


def test_checkpoint_rejects_date_without_utc_time():
    from pydantic import ValidationError

    from ra_agent.audit import AuditCheckpoint

    with pytest.raises(ValidationError):
        AuditCheckpoint(
            checkpoint_id="cp",
            from_seq=1,
            to_seq=1,
            chain_head="0" * 64,
            created_at="2026-09-19Z",
            signer="key",
        )


def test_checkpoint_writer_normalizes_storage_creation_errors(audit, tmp_path):
    from ra_agent.audit import FileCheckpointStore
    from ra_agent.crypto import CryptoError

    _, _, store, signer, _ = audit
    directory = tmp_path / "not-a-directory"
    directory.write_text("file blocks directory creation", encoding="utf-8")
    blocked = FileCheckpointStore(directory, signer)
    with pytest.raises(CryptoError, match="CHECK_UNAVAILABLE"):
        blocked.save_checkpoint(store.get_trusted_checkpoint("cp-1"))


@pytest.mark.asyncio
async def test_file_evidence_recorder_persists_only_requested_objects(tmp_path):
    from ra_agent.audit import FileEvidenceRecorder
    from ra_agent.crypto import (
        CryptoError,
        EnvelopeService,
        OpenSSLSignatureProvider,
        generate_sm2_key,
    )

    key = tmp_path / "evidence-key.pem"
    public = generate_sm2_key(key)
    envelopes = EnvelopeService(
        OpenSSLSignatureProvider(
            {"evidence-key": public},
            private_keys={"evidence-key": key},
        )
    )
    directory = tmp_path / "evidence"
    recorder = FileEvidenceRecorder(directory, envelopes, key_id="evidence-key")
    payload = {
        "request_id": "request-evidence",
        "task_id": "task-evidence",
        "session_id": "session-evidence",
    }

    reference = await recorder.record_object(
        task_id="task-evidence",
        object_type="ToolCallEnvelope",
        payload=payload,
    )
    selected = await recorder.list_task_objects("task-evidence", {reference})
    assert len(selected) == 1
    assert selected[0]["payload"] == payload
    assert signed_object_digest(selected[0]["payload"], selected[0]["envelope"]) == reference

    reopened = FileEvidenceRecorder(directory, envelopes, key_id="evidence-key")
    assert await reopened.list_task_objects("task-evidence", {reference}) == selected
    with pytest.raises(CryptoError) as missing:
        await reopened.list_task_objects("task-evidence", {"f" * 64})
    assert missing.value.code == "OBJECT_MISSING"
