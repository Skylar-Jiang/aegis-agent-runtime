from __future__ import annotations

from hashlib import sha256

_ARTIFACT_PURPOSE_CODES = {
    "commit": "c",
    "quarantine": "q",
    "restore": "r",
}


def transaction_temp_token(request_id: str) -> str:
    """Return the legacy full token used to discover interrupted transactions."""

    return sha256(request_id.encode("utf-8")).hexdigest()


def transaction_artifact_name(request_id: str, *, purpose: str) -> str:
    """Return a bounded, domain-separated basename for a new transaction artifact."""

    try:
        purpose_code = _ARTIFACT_PURPOSE_CODES[purpose]
    except KeyError as exc:
        raise ValueError(f"unsupported transaction artifact purpose: {purpose}") from exc
    digest = sha256(f"AEGIS-RUNTIME:{purpose}:{request_id}".encode()).hexdigest()[:32]
    return f".aegis-{purpose_code}-{digest}"
