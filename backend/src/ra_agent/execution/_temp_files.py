from __future__ import annotations

from hashlib import sha256


def transaction_temp_token(request_id: str) -> str:
    """Return a fixed-length, filename-safe token for one request."""

    return sha256(request_id.encode("utf-8")).hexdigest()
