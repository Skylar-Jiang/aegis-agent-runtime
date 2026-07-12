from typing import Any

SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "password",
    "refresh_token",
    "secret",
    "token",
}


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if key.lower() in SENSITIVE_KEYS else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
