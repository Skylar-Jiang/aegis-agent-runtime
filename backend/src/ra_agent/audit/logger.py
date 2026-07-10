SENSITIVE_KEYS = {"api_key", "authorization", "password", "secret", "token"}


def redact(details: dict[str, object]) -> dict[str, object]:
    return {
        key: "***REDACTED***" if key.lower() in SENSITIVE_KEYS else value
        for key, value in details.items()
    }
