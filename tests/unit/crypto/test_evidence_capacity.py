import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from ra_agent.audit import FileEvidenceRecorder
from ra_agent.crypto import (
    CryptoError,
    EnvelopeService,
    OpenSSLSignatureProvider,
    generate_sm2_key,
    signed_object_digest,
)


@pytest.fixture
def evidence_stack(tmp_path):
    private = tmp_path / "key.pem"
    public = generate_sm2_key(private)
    provider = OpenSSLSignatureProvider({"key": public}, private_keys={"key": private})
    signer = EnvelopeService(provider)
    return FileEvidenceRecorder(tmp_path / "objects", signer, key_id="key"), public, private


@pytest.mark.asyncio
async def test_equivalent_objects_keep_first_envelope_across_reopen_without_private_key(
    evidence_stack,
    monkeypatch,
):
    recorder, public, private = evidence_stack
    original_sign = recorder.envelopes.signatures.sign_sm2
    signs = []

    def counted_sign(*args, **kwargs):
        signs.append(1)
        return original_sign(*args, **kwargs)

    monkeypatch.setattr(recorder.envelopes.signatures, "sign_sm2", counted_sign)
    first = await recorder.record_object(
        task_id="task", object_type="ToolCallEnvelope", payload={"task_id": "task", "n": 1}
    )
    second = await recorder.record_object(
        task_id="task", object_type="ToolCallEnvelope", payload={"n": 1.0, "task_id": "task"}
    )
    assert second == first
    assert len(signs) == 1
    private.unlink()
    reopened = FileEvidenceRecorder(
        recorder.directory,
        EnvelopeService(OpenSSLSignatureProvider({"key": public})),
        key_id="key",
    )
    assert (
        await reopened.record_object(
            task_id="task", object_type="ToolCallEnvelope", payload={"n": 1, "task_id": "task"}
        )
        == first
    )


@pytest.mark.asyncio
async def test_concurrent_equivalent_recorders_sign_once(evidence_stack, monkeypatch):
    recorder, _, _ = evidence_stack
    original_sign = recorder.envelopes.signatures.sign_sm2
    signs = []

    def counted_sign(*args, **kwargs):
        signs.append(1)
        return original_sign(*args, **kwargs)

    monkeypatch.setattr(recorder.envelopes.signatures, "sign_sm2", counted_sign)
    recorders = [
        FileEvidenceRecorder(recorder.directory, recorder.envelopes, key_id="key") for _ in range(8)
    ]
    references = await asyncio.gather(
        *[
            item.record_object(
                task_id="task", object_type="ToolCallEnvelope", payload={"task_id": "task"}
            )
            for item in recorders
        ]
    )
    assert len(set(references)) == 1
    assert len(signs) == 1
    assert len(list(recorder.directory.glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_reuse_revalidates_corrupt_stored_proof(evidence_stack):
    recorder, _, _ = evidence_stack
    payload = {"task_id": "task"}
    reference = await recorder.record_object(
        task_id="task", object_type="ToolCallEnvelope", payload=payload
    )
    path = recorder.directory / f"{reference}.json"
    stored = json.loads(path.read_bytes())
    stored["object"]["payload"]["injected"] = True
    path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(CryptoError):
        await recorder.record_object(
            task_id="task", object_type="ToolCallEnvelope", payload=payload
        )


@pytest.mark.asyncio
async def test_reuse_verifies_signature_even_with_consistent_index_and_digest(evidence_stack):
    recorder, _, _ = evidence_stack
    payload = {"task_id": "task"}
    reference = await recorder.record_object(
        task_id="task", object_type="ToolCallEnvelope", payload=payload
    )
    stored = json.loads((recorder.directory / f"{reference}.json").read_bytes())
    stored["object"]["envelope"]["signature"] = "A" * 88
    forged = signed_object_digest(stored["object"]["payload"], stored["object"]["envelope"])
    stored["object_digest"] = forged
    (recorder.directory / f"{forged}.json").write_text(json.dumps(stored), encoding="utf-8")
    connection = sqlite3.connect(recorder.directory / "object-index.sqlite3")
    try:
        with connection:
            connection.execute("UPDATE signed_objects SET reference = ?", (forged,))
    finally:
        connection.close()
    with pytest.raises(CryptoError, match="SIGNATURE_INVALID"):
        await recorder.record_object(
            task_id="task", object_type="ToolCallEnvelope", payload=payload
        )


def test_concurrent_processes_share_original_envelope(evidence_stack, tmp_path):
    recorder, public, private = evidence_stack
    keys = tmp_path / "public.json"
    keys.write_text(json.dumps({"key": public}), encoding="utf-8")
    script = """
import asyncio, json, sys
from pathlib import Path
from ra_agent.audit import FileEvidenceRecorder
from ra_agent.crypto import EnvelopeService, OpenSSLSignatureProvider
provider = OpenSSLSignatureProvider(json.loads(Path(sys.argv[1]).read_bytes()),
                                   private_keys={"key": Path(sys.argv[2])})
recorder = FileEvidenceRecorder(Path(sys.argv[3]), EnvelopeService(provider), key_id="key")
print(asyncio.run(recorder.record_object(task_id="task", object_type="ToolCallEnvelope",
                                       payload={"task_id": "task"})))
"""

    def run(_):
        return subprocess.run(
            [sys.executable, "-c", script, str(keys), str(private), str(recorder.directory)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[3] / "backend/src")},
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(run, range(3)))
    assert all(result.returncode == 0 for result in results), [r.stderr for r in results]
    assert len({result.stdout.strip() for result in results}) == 1
    assert len(list(recorder.directory.glob("*.json"))) == 1


@pytest.mark.asyncio
async def test_equivalence_preserves_object_type_key_and_task_boundaries(evidence_stack):
    recorder, public, private = evidence_stack
    alias = FileEvidenceRecorder(
        recorder.directory,
        EnvelopeService(
            OpenSSLSignatureProvider(
                {"key": public, "rotated": public},
                private_keys={"rotated": private},
            )
        ),
        key_id="rotated",
    )
    references = [
        await item.record_object(task_id=task, object_type=kind, payload={"value": 1})
        for item, task, kind in [
            (recorder, "task", "ToolCallEnvelope"),
            (recorder, "task", "ToolResult"),
            (recorder, "other-task", "ToolCallEnvelope"),
            (alias, "task", "ToolCallEnvelope"),
        ]
    ]
    assert len(set(references)) == 4


@pytest.mark.parametrize("code", ["CHECK_UNAVAILABLE", "KEY_UNAVAILABLE", "INPUT_UNAVAILABLE"])
@pytest.mark.parametrize("failure_at", ["checkpoint_store", "signature_provider"])
def test_cli_environment_failure_in_verifier_exits_two(monkeypatch, capsys, code, failure_at):
    from ra_agent.audit.__main__ import main
    from ra_agent.audit.checkpoints import FileCheckpointStore

    fixture = Path(__file__).parents[2] / "fixtures/core/crypto"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            "--bundle",
            str(fixture / "bundle.json"),
            "--keys",
            str(fixture / "public_keys.json"),
            "--checkpoints",
            str(fixture / "trusted_checkpoints"),
            "--checkpoint-id",
            "fixture-cp-1",
            "--task-id",
            "fixture-task-1",
        ],
    )

    def unavailable(*args, **kwargs):
        raise CryptoError(code)

    if failure_at == "checkpoint_store":
        monkeypatch.setattr(FileCheckpointStore, "get_trusted_checkpoint", unavailable)
    else:
        monkeypatch.setattr(OpenSSLSignatureProvider, "verify_sm2", unavailable)
    assert main() == 2
    assert json.loads(capsys.readouterr().out)["errors"][0]["code"] == code


def test_checkpoint_storage_io_error_is_environment_failure(tmp_path):
    from ra_agent.audit import FileCheckpointStore

    fixture = Path(__file__).parents[2] / "fixtures/core/crypto"
    readonly = EnvelopeService(
        OpenSSLSignatureProvider(json.loads((fixture / "public_keys.json").read_bytes()))
    )
    blocked = tmp_path / "file"
    blocked.write_text("blocks directory access", encoding="utf-8")
    store = FileCheckpointStore(blocked, readonly)
    with pytest.raises(CryptoError, match="CHECK_UNAVAILABLE"):
        store.get_trusted_checkpoint("fixture-cp-1")


def test_cli_malformed_evidence_exits_one(tmp_path, monkeypatch, capsys):
    from ra_agent.audit.__main__ import main

    fixture = Path(__file__).parents[2] / "fixtures/core/crypto"
    bundle = tmp_path / "bundle.json"
    bundle.write_text('{"duplicate":1,"duplicate":2}', encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify",
            "--bundle",
            str(bundle),
            "--keys",
            str(fixture / "public_keys.json"),
            "--checkpoints",
            str(fixture / "trusted_checkpoints"),
            "--checkpoint-id",
            "fixture-cp-1",
            "--task-id",
            "fixture-task-1",
        ],
    )
    assert main() == 1
    assert json.loads(capsys.readouterr().out)["valid"] is False


def test_direct_verifier_and_chain_export_enforce_bundle_limit(monkeypatch):
    from ra_agent.audit import AuditVerifier, FileCheckpointStore, HashChain, integrity

    fixture = Path(__file__).parents[2] / "fixtures/core/crypto"
    bundle = json.loads((fixture / "bundle.json").read_bytes())
    readonly = EnvelopeService(
        OpenSSLSignatureProvider(json.loads((fixture / "public_keys.json").read_bytes()))
    )
    verifier = AuditVerifier(
        readonly, FileCheckpointStore(fixture / "trusted_checkpoints", readonly)
    )
    monkeypatch.setattr(integrity, "MAX_AUDIT_BUNDLE_BYTES", 1000, raising=False)
    result = verifier.verify_bundle(
        bundle,
        task_id="fixture-task-1",
        trusted_checkpoint_id="fixture-cp-1",
    )
    assert not result.valid
    assert result.errors[0].code == "INPUT_TOO_LARGE"
    chain = HashChain(bundle["task_id"])
    for entry in bundle["entries"]:
        chain.append_event(entry["event"])
    anchor = {key: bundle[key] for key in ("task_id", "checkpoint", "signed_checkpoint")}
    with pytest.raises(CryptoError, match="INPUT_TOO_LARGE"):
        chain.export_bundle(anchor, objects=bundle["objects"])
