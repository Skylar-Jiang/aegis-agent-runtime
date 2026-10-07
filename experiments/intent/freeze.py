"""Freeze pilot configuration before inspecting held-out test outputs."""
import hashlib
import json
from datetime import UTC, datetime

from .dataset import ROOT, load


def fingerprints():
    paths = [ROOT / "dataset.py", ROOT / "evaluate.py", ROOT / "data/pilot.jsonl",
             ROOT.parents[1] / "backend/src/ra_agent/security/intent_boundary.py",
             ROOT.parents[1] / "backend/src/ra_agent/tools/path_resolver.py"]
    return {str(p.relative_to(ROOT.parents[1])): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def check_lock(threshold):
    value = json.loads((ROOT / "pilot-lock.json").read_text(encoding="utf-8"))
    if value["threshold"] != threshold or value["source_checksums"] != fingerprints():
        raise ValueError("pilot lock mismatch; new version and new held-out test groups required")


if __name__ == "__main__":
    load()
    value = {"status": "synthetic_pilot_only_not_formal_test", "threshold": 3, "window": 8,
             "created_at": datetime.now(UTC).isoformat(), "source_checksums": fingerprints(),
             "selection_basis": "predefined transparent fixture baseline; no optimization claim"}
    with (ROOT / "pilot-lock.json").open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
