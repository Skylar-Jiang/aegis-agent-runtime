import json
import subprocess
import sys
from pathlib import Path


def test_experiment_runner_records_real_mode_metrics(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    output_dir = tmp_path / "results"

    completed = subprocess.run(
        [
            sys.executable,
            "experiments/runners/run_experiment.py",
            "--mode",
            "all",
            "--output-dir",
            str(output_dir),
        ],
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "[BASELINE]" in completed.stdout
    assert "[FULL_GUARD]" in completed.stdout
    assert "[ADAPTIVE_RUNTIME]" in completed.stdout
    records = [
        record
        for path in output_dir.glob("*.json")
        for record in json.loads(path.read_text(encoding="utf-8"))
    ]
    assert {record["mode"] for record in records} == {
        "BASELINE",
        "FULL_GUARD",
        "ADAPTIVE_RUNTIME",
    }
    assert all("tool_executed" in record for record in records)
    assert all("check_count" in record for record in records)
    assert all(record["temporary_artifact_count"] == 0 for record in records)
    assert all(record["token_usage"].startswith("N/A") for record in records)
