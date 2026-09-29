from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_v5_runner_emits_target_lineage_and_recovery_evidence(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "v5"
    completed = subprocess.run(
        [
            sys.executable,
            root / "experiments" / "v2" / "runners" / "run_v5_capability_evidence.py",
            "--output-directory",
            output,
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    raw = output / "raw" / "v5-capability-full.jsonl"
    rows = [json.loads(line) for line in raw.read_text(encoding="utf-8").splitlines()]
    assert {row["evidence_set"] for row in rows} >= {
        "runtime_decision",
        "effect_target_conflict",
        "planner_perturbation",
        "selective_rollback",
    }
    assert any(
        row.get("inferred_conflicts", {}).get("right", {}).get("reason")
        == "subtree_read_write"
        for row in rows
        if row["evidence_set"] == "effect_target_conflict"
    )
    assert any(
        len(row.get("affected_closure", [])) == 2
        and row.get("preservation_correct") is True
        for row in rows
        if row["evidence_set"] == "selective_rollback"
    )
