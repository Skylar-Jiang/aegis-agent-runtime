"""Trusted local policy releases. Missing file uses preset; malformed release fails closed."""

import json
from pathlib import Path

from .intent import IntentPolicy


def load_policy(path: Path) -> IntentPolicy:
    if not path.exists():
        return IntentPolicy()
    release = json.loads(path.read_text(encoding="utf-8"))
    if release.get("schema_version") != "intent-policy-release-v1":
        raise ValueError("Unsupported Intent policy release")
    if release.get("kind") == "preset":
        if release.get("version") != "intent-policy:1" or release.get("repeat_limit") != 3:
            raise ValueError("Invalid preset rollback")
        return IntentPolicy()
    reviewers = release.get("reviewers", [])
    if (
        len(set(reviewers)) < 2
        or not release.get("approved_by")
        or not release.get("validation_ref")
    ):
        raise ValueError("Intent policy requires approval and validation provenance")
    if not 2 <= release["repeat_limit"] <= 5:
        raise ValueError("Intent repetition threshold outside bounded release range")
    return IntentPolicy(version=release["version"], repeat_limit=release["repeat_limit"])
