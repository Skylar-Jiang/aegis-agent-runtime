"""Offline public-key-only verifier: python -m ra_agent.audit --help."""

from __future__ import annotations

import argparse
from pathlib import Path

from ra_agent.crypto import CryptoError, EnvelopeService, OpenSSLSignatureProvider, load_json

from .checkpoints import FileCheckpointStore
from .integrity import MAX_AUDIT_BUNDLE_BYTES
from .verifier import (
    VERIFICATION_UNAVAILABLE_CODES,
    AuditVerifier,
    VerificationIssue,
    VerificationResult,
)


def _read(path: Path, *, limit: int) -> bytes:
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise CryptoError("INPUT_TOO_LARGE", f"input exceeds {limit} bytes")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify an Aegis Core v1 anchored evidence bundle")
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument(
        "--keys", required=True, type=Path, help="Trusted key_id -> SPKI PEM JSON map"
    )
    parser.add_argument("--checkpoints", required=True, type=Path, help="External trusted store")
    parser.add_argument("--checkpoint-id", required=True, help="Expected ID from trusted channel")
    parser.add_argument("--task-id", required=True, help="Expected task from trusted channel")
    args = parser.parse_args()
    configuring = True
    try:
        keys = load_json(_read(args.keys, limit=1024 * 1024))
        if not isinstance(keys, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in keys.items()
        ):
            raise CryptoError("KEY_INVALID")
        envelopes = EnvelopeService(OpenSSLSignatureProvider(keys))
        store = FileCheckpointStore(args.checkpoints, envelopes)
        configuring = False
        result = AuditVerifier(envelopes, store).verify_bundle(
            load_json(_read(args.bundle, limit=MAX_AUDIT_BUNDLE_BYTES)),
            trusted_checkpoint_id=args.checkpoint_id,
            task_id=args.task_id,
        )
    except (CryptoError, OSError) as exc:
        code = exc.code if isinstance(exc, CryptoError) else "INPUT_UNAVAILABLE"
        result = VerificationResult(
            valid=False,
            errors=[
                VerificationIssue(
                    code=code,
                    message="Cannot load/validate verifier inputs; check files and OpenSSL 3",
                )
            ],
        )
        print(result.model_dump_json())
        return 2 if configuring or code in VERIFICATION_UNAVAILABLE_CODES else 1
    print(result.model_dump_json())
    if result.valid:
        return 0
    return 2 if any(error.code in VERIFICATION_UNAVAILABLE_CODES for error in result.errors) else 1


if __name__ == "__main__":
    raise SystemExit(main())
