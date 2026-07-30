from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

from ra_agent.contracts import ExperimentResult


MODES = ("BASELINE", "FULL_GUARD", "ADAPTIVE_RUNTIME")


def _runner(repo_root: Path) -> Path:
    return repo_root / "experiments" / "v2" / "runners" / "run_rollback_benchmark.py"


def _run(
    *,
    repo_root: Path,
    output_dir: Path,
    workspace_root: Path,
    case_id: str | None = None,
    all_cases: bool = False,
    mode: str,
    reset_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(_runner(repo_root)),
    ]
    if all_cases:
        command.append("--all-cases")
    elif case_id is not None:
        command.extend(("--case-id", case_id))
    else:
        raise ValueError("case_id or all_cases is required")
    command.extend(
        (
            "--mode",
            mode,
            "--repetitions",
            "1",
            "--output-dir",
            str(output_dir),
            "--workspace-root",
            str(workspace_root),
        )
    )
    if reset_output:
        command.append("--reset-output")
    return subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _load_jsonl(path: Path) -> list[ExperimentResult]:
    return [
        ExperimentResult.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_v2_rollback_runner_emits_all_cases_and_modes(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    output_dir = tmp_path / "raw"
    workspace_root = tmp_path / "work"

    for index, mode in enumerate(MODES):
        completed = _run(
            repo_root=repo_root,
            output_dir=output_dir,
            workspace_root=workspace_root,
            all_cases=True,
            mode=mode,
            reset_output=index == 0,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout

    jsonl_path = output_dir / "rollback_benchmark.jsonl"
    csv_path = output_dir / "rollback_benchmark.csv"
    results = _load_jsonl(jsonl_path)
    assert len(results) == 21
    assert {(result.case_id, result.mode.value) for result in results} == {
        (f"M3-C0{case_number}", mode)
        for case_number in range(1, 8)
        for mode in MODES
    }
    for result in results:
        assert result.status == result.expected_status
        raw_result_path = result.raw_result_path
        assert raw_result_path is not None
        assert raw_result_path.endswith("rollback_benchmark.jsonl")
        assert result.rollback_elapsed_ms >= 0
        assert result.metrics.keys() == {
            "affected_node_count",
            "rolled_back_effect_count",
            "preserved_node_count",
            "preserved_effect_count",
        }

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    assert len(csv_rows) == 21
    assert {row["run_id"] for row in csv_rows} == {
        result.run_id for result in results
    }


def test_mode_scope_changes_preserved_effects(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    output_dir = tmp_path / "raw"
    workspace_root = tmp_path / "work"

    for index, mode in enumerate(MODES):
        completed = _run(
            repo_root=repo_root,
            output_dir=output_dir,
            workspace_root=workspace_root,
            case_id="M3-C01",
            mode=mode,
            reset_output=index == 0,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout

    by_mode = {result.mode.value: result for result in _load_jsonl(
        output_dir / "rollback_benchmark.jsonl"
    )}
    assert by_mode["BASELINE"].metrics["rolled_back_effect_count"] == 2
    assert by_mode["BASELINE"].metrics["preserved_effect_count"] == 0
    assert by_mode["FULL_GUARD"].metrics["rolled_back_effect_count"] == 2
    assert by_mode["FULL_GUARD"].metrics["preserved_effect_count"] == 0
    assert by_mode["ADAPTIVE_RUNTIME"].metrics["rolled_back_effect_count"] == 1
    assert by_mode["ADAPTIVE_RUNTIME"].metrics["preserved_effect_count"] == 1


def test_boundary_and_failure_case_facts(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    output_dir = tmp_path / "raw"
    workspace_root = tmp_path / "work"

    selected = (
        ("M3-C03", "BASELINE"),
        ("M3-C03", "ADAPTIVE_RUNTIME"),
        ("M3-C04", "ADAPTIVE_RUNTIME"),
        ("M3-C05", "ADAPTIVE_RUNTIME"),
        ("M3-C06", "ADAPTIVE_RUNTIME"),
    )
    for index, (case_id, mode) in enumerate(selected):
        completed = _run(
            repo_root=repo_root,
            output_dir=output_dir,
            workspace_root=workspace_root,
            case_id=case_id,
            mode=mode,
            reset_output=index == 0,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout

    results = {
        (result.case_id, result.mode.value): result
        for result in _load_jsonl(output_dir / "rollback_benchmark.jsonl")
    }
    baseline_commit = results[("M3-C03", "BASELINE")]
    assert baseline_commit.status == "FAILED"
    assert baseline_commit.rollback_count == 0
    assert baseline_commit.residual_effect_count == 1

    adaptive_commit = results[("M3-C03", "ADAPTIVE_RUNTIME")]
    assert adaptive_commit.status == "ROLLED_BACK"
    assert adaptive_commit.rollback_count == 1
    assert adaptive_commit.residual_effect_count == 0

    conflict = results[("M3-C04", "ADAPTIVE_RUNTIME")]
    assert conflict.status == "FAILED"
    assert conflict.rollback_count == 2
    assert conflict.residual_effect_count == 1
    assert conflict.safety_outcome == "PASS_USER_CHANGE_PRESERVED_PARTIAL_FAILURE"

    idempotent = results[("M3-C05", "ADAPTIVE_RUNTIME")]
    assert idempotent.status == "SUCCESS"
    assert idempotent.selective_rollback_count == 2
    assert idempotent.rollback_count == 1

    rejected = results[("M3-C06", "ADAPTIVE_RUNTIME")]
    assert rejected.status == "BLOCKED"
    assert rejected.blocked_count == 1
    assert rejected.rollback_count == 0
    assert rejected.metrics["preserved_effect_count"] == 2
