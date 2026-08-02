from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ra_agent.contracts import ExperimentResult


def test_formal_graph_benchmark_runner_emits_contract_valid_runtime_measurements(
    tmp_path: Path,
) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    output_directory = tmp_path / "raw"
    runner = repository_root / "experiments" / "v2" / "runners" / "run_graph_benchmarks.py"

    completed = subprocess.run(
        [
            sys.executable,
            str(runner),
            "--output-directory",
            str(output_directory),
            "--output-stem",
            "graph-benchmark-test",
            "--repetitions",
            "1",
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    rows = [
        ExperimentResult.model_validate_json(line)
        for line in (output_directory / "graph-benchmark-test.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert {(row.fixture_id, row.mode.value) for row in rows} == {
        ("parallel-dag", "BASELINE"),
        ("parallel-dag", "FULL_GUARD"),
        ("parallel-dag", "ADAPTIVE_RUNTIME"),
        ("approval-wait", "BASELINE"),
        ("approval-wait", "FULL_GUARD"),
        ("approval-wait", "ADAPTIVE_RUNTIME"),
    }
    assert all(row.graph_elapsed_ms >= 0 for row in rows)
    assert all(row.parallel_saved_ms >= 0 for row in rows)
    assert all(
        row.parallel_saved_ms
        == max(0, row.metrics["sum_node_elapsed_ms"] - row.graph_elapsed_ms)
        for row in rows
    )

    approval = {row.mode.value: row for row in rows if row.fixture_id == "approval-wait"}
    assert approval["FULL_GUARD"].metrics["nodes_completed_during_approval"] == 0
    assert approval["ADAPTIVE_RUNTIME"].metrics["nodes_completed_during_approval"] >= 1
    assert approval["ADAPTIVE_RUNTIME"].metrics["hidden_approval_wait_ms"] > 0

    parallel = {row.mode.value: row for row in rows if row.fixture_id == "parallel-dag"}
    assert parallel["ADAPTIVE_RUNTIME"].metrics["max_observed_concurrency"] >= 2
    assert parallel["ADAPTIVE_RUNTIME"].parallel_saved_ms > 0

    summary = json.loads(
        (output_directory.parent / "derived" / "graph-benchmark-test-summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["source_row_count"] == len(rows)
    assert summary["by_fixture_mode"]["parallel-dag:ADAPTIVE_RUNTIME"]["sample_count"] == 1
