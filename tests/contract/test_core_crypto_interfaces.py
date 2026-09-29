"""P1/P2/P3 integration contracts: real and fake providers share call signatures."""

import json
from pathlib import Path

import pytest

from ra_agent.audit import AuditCheckpoint, VerificationResult
from ra_agent.audit.integrity import AuditBundle, CheckpointRecord
from ra_agent.crypto import (
    EnvelopeService,
    OpenSSLSignatureProvider,
    SignedEnvelope,
    generate_sm2_key,
    load_json,
)
from ra_agent.contracts.core_v1 import TaskContractV2
from ra_agent.gateway.fakes import FakeSignatureProvider
from ra_agent.gateway.interfaces import SignatureProvider


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_signature_provider_same_public_contract(kind, tmp_path):
    provider: SignatureProvider
    key_id = "contract-test"
    if kind == "real":
        key = tmp_path / "key.pem"
        pem = generate_sm2_key(key)
        provider = OpenSSLSignatureProvider({key_id: pem}, private_keys={key_id: key})
    else:
        provider = FakeSignatureProvider(key_id)
    signature = provider.sign_sm2(b"contract bytes", key_id=key_id)
    assert isinstance(signature, str)
    assert provider.verify_sm2(b"contract bytes", signature, key_id=key_id)
    assert not provider.verify_sm2(b"other bytes", signature, key_id=key_id)
    assert not provider.verify_sm2(b"contract bytes", signature, key_id="unknown")
    assert isinstance(provider.list_public_keys()[key_id], str)


@pytest.mark.parametrize(
    "model",
    [
        SignedEnvelope,
        AuditCheckpoint,
        CheckpointRecord,
        AuditBundle,
        VerificationResult,
    ],
)
def test_published_schema_matches_implementation(model):
    root = Path(__file__).resolve().parents[2]
    published = root / "docs/interfaces/schemas" / f"{model.__name__}.schema.json"
    assert (
        json.loads(published.read_text(encoding="utf-8")) == model.model_json_schema()
    )


def test_frozen_envelope_fixture_verifies():
    root = Path(__file__).resolve().parents[1] / "fixtures/core/crypto"
    keys = load_json((root / "public_keys.json").read_bytes())
    obj = load_json((root / "signed_envelope.json").read_bytes())
    service = EnvelopeService(OpenSSLSignatureProvider(keys))
    assert service.verify(obj["payload"], obj["envelope"], object_type="TaskContractV2")


def test_frozen_signed_contract_is_a_complete_p1_model():
    root = Path(__file__).resolve().parents[1] / "fixtures/core/crypto"
    obj = load_json((root / "signed_envelope.json").read_bytes())
    assert TaskContractV2.model_validate(obj["payload"]).task_id == "fixture-task-1"
