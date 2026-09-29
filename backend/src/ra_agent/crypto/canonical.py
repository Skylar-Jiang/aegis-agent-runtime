"""Strict JSON input and RFC 8785 bytes. No inferred business-field selection."""

from __future__ import annotations

import json
from typing import Any

import rfc8785

CRYPTO_UNAVAILABLE_CODES = frozenset(
    {"CHECK_UNAVAILABLE", "KEY_UNAVAILABLE", "KEY_STORAGE_ERROR", "INPUT_UNAVAILABLE"}
)


class CryptoError(ValueError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


class Canonicalizer:
    def canonicalize(self, value: Any) -> bytes:
        try:
            return rfc8785.dumps(value)
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            raise CryptoError("CANONICALIZATION_INVALID") from exc


def load_json(raw: str | bytes) -> Any:
    """Reject duplicate keys before a dict can silently erase them."""

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise CryptoError("CANONICALIZATION_INVALID", "duplicate JSON key")
            result[key] = value
        return result

    try:
        result = json.loads(raw, object_pairs_hook=unique)
        Canonicalizer().canonicalize(result)
        return result
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CryptoError("CANONICALIZATION_INVALID", "invalid JSON") from exc
