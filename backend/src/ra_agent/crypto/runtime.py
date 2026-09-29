"""Fail-closed construction of the Core v1 cryptographic evidence stack."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ra_agent.audit import (
    AuditExportService,
    AuditVerifier,
    FileCheckpointStore,
    FileEvidenceRecorder,
)
from ra_agent.core.config import CoreCryptoMode, Settings
from ra_agent.events import SqliteEventStore
from ra_agent.gateway import EvidenceRecorder, FakeSignatureProvider, SignatureProvider

from .canonical import CryptoError, load_json
from .envelope import EnvelopeService
from .openssl import OpenSSLSignatureProvider

MAX_PUBLIC_KEYS_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class CoreCryptoRuntime:
    mode: CoreCryptoMode
    signature_provider: SignatureProvider
    event_store: SqliteEventStore
    evidence_recorder: EvidenceRecorder | None
    audit_exporter: AuditExportService | None
    audit_verifier: AuditVerifier | None


def build_core_crypto_runtime(settings: Settings) -> CoreCryptoRuntime:
    """Build one P2/P3-compatible runtime.

    Even FAKE mode uses SQLite persistence and the standard SM3 chain so gateway/event
    integration is exercised in development.  Signed evidence/export is deliberately
    unavailable until ``CORE_CRYPTO_MODE=sm2`` supplies trusted SM2 keys.
    """

    if settings.core_crypto_mode is CoreCryptoMode.FAKE:
        return CoreCryptoRuntime(
            mode=CoreCryptoMode.FAKE,
            signature_provider=FakeSignatureProvider(),
            event_store=SqliteEventStore(
                settings.core_event_log_path.with_suffix(".sqlite3"),
                legacy_path=settings.core_event_log_path,
            ),
            evidence_recorder=None,
            audit_exporter=None,
            audit_verifier=None,
        )

    key_id = settings.core_sm2_key_id
    public_path = settings.core_sm2_public_keys_path
    private_path = settings.core_sm2_private_key_path
    if (
        not key_id
        or public_path is None
        or private_path is None
        or not Path(public_path).is_file()
        or not Path(private_path).is_file()
    ):
        raise CryptoError(
            "KEY_UNAVAILABLE",
            "SM2 mode requires a key_id, trusted public-key file and private-key file",
        )
    public_keys = _read_public_keys(Path(public_path))
    if key_id not in public_keys:
        raise CryptoError("KEY_UNKNOWN", "configured key_id is absent from trusted public keys")

    provider = OpenSSLSignatureProvider(
        public_keys,
        private_keys={key_id: Path(private_path)},
    )
    envelopes = EnvelopeService(provider)
    event_store = SqliteEventStore(
        settings.core_event_log_path.with_suffix(".sqlite3"),
        legacy_path=settings.core_event_log_path,
    )
    evidence = FileEvidenceRecorder(
        settings.core_evidence_root,
        envelopes,
        key_id=key_id,
    )
    checkpoints = FileCheckpointStore(
        settings.core_audit_checkpoint_root,
        envelopes,
    )
    exporter = AuditExportService(
        event_store=event_store,
        evidence_recorder=evidence,
        envelopes=envelopes,
        checkpoints=checkpoints,
        key_id=key_id,
    )
    # Verification has public-key capabilities only. The signing provider remains
    # in this backend process; this separation does not claim OS/key isolation.
    readonly = EnvelopeService(OpenSSLSignatureProvider(public_keys))
    verifier = AuditVerifier(
        readonly,
        FileCheckpointStore(settings.core_audit_checkpoint_root, readonly),
    )
    return CoreCryptoRuntime(
        mode=CoreCryptoMode.SM2,
        signature_provider=provider,
        event_store=event_store,
        evidence_recorder=evidence,
        audit_exporter=exporter,
        audit_verifier=verifier,
    )


def _read_public_keys(path: Path) -> dict[str, str]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_PUBLIC_KEYS_BYTES + 1)
    except OSError as exc:
        raise CryptoError("KEY_UNAVAILABLE", "cannot read trusted public keys") from exc
    if len(raw) > MAX_PUBLIC_KEYS_BYTES:
        raise CryptoError("INPUT_TOO_LARGE", "trusted public-key file exceeds 1 MiB")
    value: Any = load_json(raw)
    if not isinstance(value, dict) or not value or not all(
        isinstance(key, str) and isinstance(pem, str) for key, pem in value.items()
    ):
        raise CryptoError("KEY_INVALID", "trusted public keys must be a key_id to PEM object")
    return value
