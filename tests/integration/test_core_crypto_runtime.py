import asyncio
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from ra_agent.audit import AuditExportService, FileCheckpointStore, FileEvidenceRecorder, HashChain
from ra_agent.audit.integrity import encode_bundle
from ra_agent.crypto import CryptoError, EnvelopeService, OpenSSLSignatureProvider, generate_sm2_key
from ra_agent.events import JsonlEventStore


def _event(sequence, reference):
    return {
        "event_id": f"event-{sequence}",
        "sequence": sequence,
        "task_id": "task",
        "parent_event_id": None,
        "type": "EXECUTION_STARTED",
        "actor": "gateway",
        "source_ref": f"request-{sequence}",
        "object_digest": reference,
        "state": "EXECUTING",
        "decision": "ALLOW",
        "result_digest": None,
        "occurred_at": "2026-09-23T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_large_valid_export_roundtrips_public_only_cli(tmp_path):
    private = tmp_path / "key.pem"
    public = generate_sm2_key(private)
    signer = EnvelopeService(
        OpenSSLSignatureProvider({"key": public}, private_keys={"key": private})
    )
    evidence = FileEvidenceRecorder(tmp_path / "objects", signer, key_id="key")
    events = JsonlEventStore(tmp_path / "events.jsonl", chain_factory=HashChain)
    checkpoints = FileCheckpointStore(tmp_path / "checkpoints", signer)
    # Eighteen distinct 1 MiB calls remain a valid v1 bundle. This also covers
    # historical bundles made by six calls whose envelopes were signed three times.
    for i in range(18):
        reference = await evidence.record_object(
            task_id="task",
            object_type="ToolCallEnvelope",
            payload={"task_id": "task", "request_id": f"request-{i}", "content": "x" * 1024**2},
        )
        await events.append_event(_event(i + 1, reference))
    exporter = AuditExportService(
        event_store=events,
        evidence_recorder=evidence,
        envelopes=signer,
        checkpoints=checkpoints,
        key_id="key",
    )
    bundle = await exporter.export_task(task_id="task", checkpoint_id="large")
    bundle_path = tmp_path / "bundle.json"
    bundle_path.write_bytes(encode_bundle(bundle))
    assert bundle_path.stat().st_size > 16 * 1024**2
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps({"key": public}), encoding="utf-8")
    private.unlink()
    result = await asyncio.to_thread(
        subprocess.run,
        [
            sys.executable,
            "-m",
            "ra_agent.audit",
            "--bundle",
            str(bundle_path),
            "--keys",
            str(keys),
            "--checkpoints",
            str(checkpoints.directory),
            "--checkpoint-id",
            "large",
            "--task-id",
            "task",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "backend/src")},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["verified_events"] == 18


def test_runtime_verifier_does_not_have_signing_capability(tmp_path):
    from ra_agent.core.config import CoreCryptoMode, RuntimeMode, Settings
    from ra_agent.crypto.runtime import build_core_crypto_runtime

    private = tmp_path / "key.pem"
    public = generate_sm2_key(private)
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps({"key": public}), encoding="utf-8")
    runtime = build_core_crypto_runtime(
        Settings(
            runtime_mode=RuntimeMode.OFFLINE,
            core_crypto_mode=CoreCryptoMode.SM2,
            core_sm2_key_id="key",
            core_sm2_public_keys_path=keys,
            core_sm2_private_key_path=private,
            core_event_log_path=tmp_path / "events.jsonl",
            core_evidence_root=tmp_path / "objects",
            core_audit_checkpoint_root=tmp_path / "checkpoints",
        )
    )
    assert runtime.audit_verifier is not None
    with pytest.raises(CryptoError, match="KEY_UNAVAILABLE"):
        runtime.audit_verifier.envelopes.sign(
            {"task_id": "task"},
            object_type="ToolCallEnvelope",
            key_id="key",
        )


@pytest.mark.asyncio
async def test_export_rejects_oversize_before_persisting_checkpoint(tmp_path, monkeypatch):
    from ra_agent.audit import integrity

    private = tmp_path / "key.pem"
    public = generate_sm2_key(private)
    signer = EnvelopeService(
        OpenSSLSignatureProvider({"key": public}, private_keys={"key": private})
    )
    evidence = FileEvidenceRecorder(tmp_path / "objects", signer, key_id="key")
    events = JsonlEventStore(tmp_path / "events.jsonl", chain_factory=HashChain)
    checkpoints = FileCheckpointStore(tmp_path / "checkpoints", signer)
    reference = await evidence.record_object(
        task_id="task",
        object_type="ToolCallEnvelope",
        payload={"task_id": "task", "content": "x" * 2048},
    )
    await events.append_event(_event(1, reference))
    monkeypatch.setattr(integrity, "MAX_AUDIT_BUNDLE_BYTES", 1000, raising=False)
    exporter = AuditExportService(
        event_store=events,
        evidence_recorder=evidence,
        envelopes=signer,
        checkpoints=checkpoints,
        key_id="key",
    )
    with pytest.raises(CryptoError, match="INPUT_TOO_LARGE"):
        await exporter.export_task(task_id="task", checkpoint_id="oversized")
    with pytest.raises(CryptoError, match="ANCHOR_NOT_FOUND"):
        checkpoints.get_trusted_checkpoint("oversized")


@pytest.mark.asyncio
async def test_export_crypto_work_runs_outside_event_loop(tmp_path, monkeypatch):
    from ra_agent.audit import exporter as exporter_module

    private = tmp_path / "key.pem"
    public = generate_sm2_key(private)
    signer = EnvelopeService(
        OpenSSLSignatureProvider({"key": public}, private_keys={"key": private})
    )
    evidence = FileEvidenceRecorder(tmp_path / "objects", signer, key_id="key")
    events = JsonlEventStore(tmp_path / "events.jsonl", chain_factory=HashChain)
    checkpoints = FileCheckpointStore(tmp_path / "checkpoints", signer)
    reference = await evidence.record_object(
        task_id="task",
        object_type="ToolCallEnvelope",
        payload={"task_id": "task"},
    )
    await events.append_event(_event(1, reference))
    loop_thread = threading.get_ident()
    chain_threads, digest_threads = [], []
    real_append = HashChain.append_event
    real_digest = exporter_module.signed_object_digest

    def append_in_thread(*args, **kwargs):
        chain_threads.append(threading.get_ident())
        return real_append(*args, **kwargs)

    def digest_in_thread(*args, **kwargs):
        digest_threads.append(threading.get_ident())
        return real_digest(*args, **kwargs)

    monkeypatch.setattr(HashChain, "append_event", append_in_thread)
    monkeypatch.setattr(exporter_module, "signed_object_digest", digest_in_thread)
    exporter = AuditExportService(
        event_store=events,
        evidence_recorder=evidence,
        envelopes=signer,
        checkpoints=checkpoints,
        key_id="key",
    )
    bundle = await exporter.export_task(task_id="task", checkpoint_id="threaded")
    assert len(bundle["entries"]) == 1 and len(bundle["objects"]) == 1
    assert chain_threads and digest_threads
    assert loop_thread not in chain_threads + digest_threads
