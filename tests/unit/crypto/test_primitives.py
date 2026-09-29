"""Public P3 contracts; real OpenSSL operations, no signature mocks."""

import base64
import importlib.util
import json
import subprocess

import pytest


def test_crypto_module_exists():
    assert importlib.util.find_spec("ra_agent.crypto") is not None


def test_jcs_rfc8785_number_and_unicode_rules():
    from ra_agent.crypto import Canonicalizer

    canonical = Canonicalizer()
    assert canonical.canonicalize({"z": -0.0, "a": [1e-7, 1e20, 1e21, 4.50]}) == (
        b'{"a":[1e-7,100000000000000000000,1e+21,4.5],"z":0}'
    )
    assert canonical.canonicalize({"\ue000": 2, "\U0001f600": 1}) == (
        '{"😀":1,"\ue000":2}'.encode()
    )
    assert canonical.canonicalize({"a": "é"}) != canonical.canonicalize(
        {"a": "e\u0301"}
    )


@pytest.mark.parametrize(
    "value", [float("nan"), float("inf"), 2**53, {1: "x"}, "\ud800"]
)
def test_jcs_rejects_ambiguous_or_non_json_values(value):
    from ra_agent.crypto import Canonicalizer, CryptoError

    with pytest.raises(CryptoError, match="CANONICALIZATION_INVALID"):
        Canonicalizer().canonicalize(value)


@pytest.mark.parametrize("raw", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}'])
def test_json_reader_rejects_ambiguous_input(raw):
    from ra_agent.crypto import CryptoError, load_json

    with pytest.raises(CryptoError):
        load_json(raw)


def test_sm3_known_vectors():
    from ra_agent.crypto import DigestProvider

    digest = DigestProvider()
    assert (
        digest.sm3(b"abc")
        == "66c7f0f462eeedd9d1f2d46bdc10e4e24167c4875cf2f7a2297da02b8f4ba8e0"
    )
    assert digest.sm3(b"abcd" * 16) == (
        "debe9ff92275b8a138604889c18e5a4d6fdb70e5387e5765293dcba39c0c5732"
    )


def test_sm2_gbt32918_annex_a_known_signature():
    # GM/T 0003.5-2012 / GB/T 32918.5-2016 Annex A, also in OpenSSL sm2_internal_test.c.
    from ra_agent.crypto import OpenSSLSignatureProvider

    x = "09f9df311e5421a150dd7d161e4bc5c672179fad1833fc076bb08ff356f35020"
    y = "ccea490ce26775a52dc6ea718cc1aa600aed05fbf35e084a6632f6072da9ad13"
    der = bytes.fromhex(
        "3059301306072a8648ce3d020106082a811ccf5501822d03420004" + x + y
    )
    pem = (
        "-----BEGIN PUBLIC KEY-----\n"
        + base64.b64encode(der).decode()
        + ("\n-----END PUBLIC KEY-----\n")
    )
    signature = bytes.fromhex(
        "3046022100f5a03b0648d2c4630eeac513e1bb81a15944da3827d5b74143ac7eaceee720b3"
        "022100b1b6aa29df212fd8763182bc0d421ca1bb9038fd1f7f42d4840b69c485bbc1aa"
    )
    verifier = OpenSSLSignatureProvider({"gbt-annex-a": pem})
    assert verifier.verify_sm2(
        b"message digest", base64.b64encode(signature).decode(), key_id="gbt-annex-a"
    )


@pytest.fixture
def provider(tmp_path):
    from ra_agent.crypto import OpenSSLSignatureProvider, generate_sm2_key

    private = tmp_path / "private.pem"
    public = generate_sm2_key(private)
    return OpenSSLSignatureProvider(
        {"p3-test": public}, private_keys={"p3-test": private}
    )


def test_sm2_real_signatures_public_only_and_tampering(provider):
    from ra_agent.crypto import OpenSSLSignatureProvider

    signature = provider.sign_sm2(b"payload", key_id="p3-test")
    verifier = OpenSSLSignatureProvider(provider.list_public_keys())
    assert verifier.verify_sm2(b"payload", signature, key_id="p3-test")
    assert not verifier.verify_sm2(b"changed", signature, key_id="p3-test")
    assert not verifier.verify_sm2(b"payload", signature, key_id="unknown")
    assert not verifier.verify_sm2(b"payload", "not-base64!", key_id="p3-test")
    assert not verifier.verify_sm2(
        b"payload", base64.b64encode(b"bad DER").decode(), key_id="p3-test"
    )
    assert "PRIVATE" not in json.dumps(provider.list_public_keys())


def test_sm2_cross_verifies_with_openssl_dgst(provider, tmp_path):
    from ra_agent.crypto import find_openssl

    pub = tmp_path / "public.pem"
    pub.write_text(provider.list_public_keys()["p3-test"], encoding="ascii")
    signature = provider.sign_sm2(b"cross implementation path", key_id="p3-test")
    sig = tmp_path / "signature.der"
    sig.write_bytes(base64.b64decode(signature))
    result = subprocess.run(
        [
            find_openssl(),
            "dgst",
            "-sm3",
            "-verify",
            str(pub),
            "-signature",
            str(sig),
            "-sigopt",
            "distid:1234567812345678",
        ],
        input=b"cross implementation path",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    wrong_id = subprocess.run(
        [
            find_openssl(),
            "dgst",
            "-sm3",
            "-verify",
            str(pub),
            "-signature",
            str(sig),
            "-sigopt",
            "distid:wrong",
        ],
        input=b"cross implementation path",
        capture_output=True,
        check=False,
    )
    assert wrong_id.returncode != 0


def test_key_validation_and_no_private_overwrite(tmp_path):
    from ra_agent.crypto import CryptoError, OpenSSLSignatureProvider, generate_sm2_key

    private = tmp_path / "key.pem"
    generate_sm2_key(private)
    before = private.read_bytes()
    with pytest.raises(CryptoError):
        generate_sm2_key(private)
    assert private.read_bytes() == before
    with pytest.raises(CryptoError):
        OpenSSLSignatureProvider({"bad": "not a PEM"})


def test_signing_key_must_match_registered_public_key(tmp_path):
    from ra_agent.crypto import CryptoError, OpenSSLSignatureProvider, generate_sm2_key

    first = tmp_path / "first.pem"
    second = tmp_path / "second.pem"
    public = generate_sm2_key(first)
    generate_sm2_key(second)
    with pytest.raises(CryptoError, match="KEY_MISMATCH"):
        OpenSSLSignatureProvider({"key": public}, private_keys={"key": second})


def test_envelope_binds_payload_and_all_metadata(provider):
    from ra_agent.crypto import EnvelopeService

    service = EnvelopeService(provider)
    payload = {"contract_id": "c-1", "version": 1}
    envelope = service.sign(payload, object_type="TaskContractV2", key_id="p3-test")
    assert set(envelope.model_dump()) == {
        "object_type",
        "schema_version",
        "algorithm",
        "key_id",
        "payload_digest",
        "signature",
    }
    assert service.verify(payload, envelope, object_type="TaskContractV2")
    assert not service.verify(
        {**payload, "version": 2}, envelope, object_type="TaskContractV2"
    )
    for field, changed in [
        ("object_type", "AuditCheckpoint"),
        ("schema_version", "2.0"),
        ("algorithm", "ECDSA"),
        ("key_id", "unknown"),
        ("payload_digest", "0" * 64),
    ]:
        bad = envelope.model_dump() | {field: changed}
        assert not service.verify(payload, bad, object_type="TaskContractV2"), field


def test_key_alias_cannot_relabel_existing_envelope(provider):
    from ra_agent.crypto import EnvelopeService, OpenSSLSignatureProvider

    env = EnvelopeService(provider).sign(
        {}, object_type="TaskContractV2", key_id="p3-test"
    )
    keys = provider.list_public_keys()
    keys["alias"] = keys["p3-test"]
    verifier = EnvelopeService(OpenSSLSignatureProvider(keys))
    assert not verifier.verify(
        {}, env.model_dump() | {"key_id": "alias"}, object_type="TaskContractV2"
    )


def test_public_only_cannot_sign(provider):
    from ra_agent.crypto import CryptoError, OpenSSLSignatureProvider

    with pytest.raises(CryptoError):
        OpenSSLSignatureProvider(provider.list_public_keys()).sign_sm2(
            b"x", key_id="p3-test"
        )
