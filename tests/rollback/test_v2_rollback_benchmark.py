from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ra_agent.contracts import ExperimentResult


@pytest.mark.parametrize(
    ("case_id", "expected_pending", "expected_preserved"),
    [
        ("M3-C01", 2, 1),
        ("M3-C02", 3, 2),
        ("M3-C07", 2, 1),
    ],
)
def test_v2_rollback_runner_emits_valid_raw_results(
    tmp_path: Path,
    case_id: str,
    expected_pending: int,
    expected_preserved: int,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    runner = repo_root / "experiments" / "v2" / "runners" / "run_rollback_benchmark.py"
    output_dir = tmp_path / "raw"
    workspace_root = tmp_path / "work"

    completed = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--case-id",
            case_id,
            "--mode",
            "ADAPTIVE_RUNTIME",
            "--repetitions",
            "1",
            "--output-dir",
            str(output_dir),
            "--workspace-root",
            str(workspace_root),
            "--reset-output",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout

    jsonl_path = output_dir / "rollback_benchmark.jsonl"
    csv_path = output_dir / "rollback_benchmark.csv"
    json_rows = [
        json.loads(line)
        for line in jsonl_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(json_rows) == 1

    result = ExperimentResult.model_validate(json_rows[0])
    assert result.case_id == case_id
    assert result.mode.value == "ADAPTIVE_RUNTIME"
    assert result.status == result.expected_status == "SUCCESS"
    assert result.error_code is None
    assert result.pending_effect_count == expected_pending
    assert result.rollback_count == 1
    assert result.selective_rollback_count == 1
    assert result.residual_effect_count == 0
    assert result.metrics["rolled_back_effect_count"] == 1
    assert result.metrics["preserved_effect_count"] == expected_preserved
    assert result.raw_result_path.endswith("rollback_benchmark.jsonl")

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert len(csv_rows) == 1
    assert csv_rows[0]["run_id"] == result.run_id
    assert csv_rows[0]["case_id"] == case_id
    assert json.loads(csv_rows[0]["metrics"]) == result.metrics


def test_v2_rollback_runner_rejects_unimplemented_modes(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    runner = repo_root / "experiments" / "v2" / "runners" / "run_rollback_benchmark.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--case-id",
            "M3-C01",
            "--mode",
            "BASELINE",
            "--repetitions",
            "1",
            "--output-dir",
            str(tmp_path / "raw"),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 1
    assert "only supports ADAPTIVE_RUNTIME" in completed.stderr
