from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_final_workflow_runner_emits_long_horizon_metrics(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    output = tmp_path / "final-workflow"
    completed = subprocess.run(
        [
            sys.executable,
            root / "experiments" / "v2" / "runners" / "run_final_workflow_benchmark.py",
            "--output-directory",
            output,
            "--repetitions",
            "1",
        ],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    rows = [
        json.loads(line)
        for line in (output / "raw" / "final-workflow.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {row["node_count"] for row in rows} == {5, 10, 20}
    assert {row["mode"] for row in rows} == {
        "BASELINE",
        "FULL_GUARD",
        "ADAPTIVE_RUNTIME",
    }
    assert any(
        row["mode"] == "ADAPTIVE_RUNTIME"
        and row["approval_actions"] < row["mutation_count"]
        and row["conflict_violation_count"] == 0
        for row in rows
    )
