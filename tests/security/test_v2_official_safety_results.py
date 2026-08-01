import csv
import json
from pathlib import Path

from ra_agent.contracts import ExperimentMode, ExperimentResult

ROOT = Path(__file__).resolve().parents[2]
RAW_JSONL = (
    ROOT / "experiments" / "v2" / "results" / "raw" / "v2-safety-74c071c-20260801.jsonl"
)
RAW_CSV = RAW_JSONL.with_suffix(".csv")
DERIVED_JSON = (
    ROOT
    / "experiments"
    / "v2"
    / "results"
    / "derived"
    / "v2-safety-74c071c-20260801-metrics.json"
)
CASE_TABLE = DERIVED_JSON.with_name("v2-safety-74c071c-20260801-cases.csv")


def test_official_safety_results_reconstruct_from_raw() -> None:
    rows = [
        ExperimentResult.model_validate_json(line)
        for line in RAW_JSONL.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    derived = json.loads(DERIVED_JSON.read_text(encoding="utf-8"))
    expected_raw_path = RAW_JSONL.relative_to(ROOT).as_posix()

    assert len(rows) == derived["sample_size"]
    assert {row.run_id for row in rows} == set(derived["run_ids"])
    assert {row.git_commit for row in rows} == set(derived["git_commits"])
    assert {row.environment_fingerprint for row in rows} == set(
        derived["environment_fingerprints"]
    )
    assert all(row.raw_result_path == expected_raw_path for row in rows)
    assert all(row.audit_event_count == 0 and row.audit_digest is None for row in rows)
    assert all(
        row.graph_elapsed_ms == 0
        and row.critical_path_ms == 0
        and row.parallel_saved_ms == 0
        and not any(row.metrics.values())
        for row in rows
    )

    signatures = {
        mode: {(row.fixture_id, row.repetition) for row in rows if row.mode is mode}
        for mode in ExperimentMode
    }
    assert all(
        signature == signatures[ExperimentMode.BASELINE]
        for signature in signatures.values()
    )

    for mode in ExperimentMode:
        mode_rows = [row for row in rows if row.mode is mode]
        unsafe_rows = [row for row in mode_rows if row.objective_class == "UNSAFE"]
        metrics = derived["metrics"][mode.value]
        assert metrics["rows"] == len(mode_rows)
        assert metrics["unsafe_tool_executed_count"] == sum(
            row.unsafe_tool_executed_count for row in mode_rows
        )
        assert metrics["false_block_count"] == sum(
            row.false_block_count for row in mode_rows
        )
        assert metrics["approval_requested_count"] == sum(
            row.approval_requested_count for row in mode_rows
        )
        assert metrics["approval_decision_count"] == sum(
            row.approval_decision_count for row in mode_rows
        )
        assert metrics["manual_action_count"] == sum(
            row.manual_action_count for row in mode_rows
        )
        assert metrics["check_count"] == sum(row.check_count for row in mode_rows)
        assert metrics["risk_escalation_count"] == sum(
            row.risk_escalation_count for row in mode_rows
        )
        assert metrics["unsafe_request_block_rate"] == round(
            sum(row.status == "BLOCKED" for row in unsafe_rows) / len(unsafe_rows),
            4,
        )

    with RAW_CSV.open("r", encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))
    with CASE_TABLE.open("r", encoding="utf-8", newline="") as handle:
        case_rows = list(csv.DictReader(handle))
    assert len(csv_rows) == len(rows)
    assert (
        len(case_rows)
        == len(signatures)
        * len(signatures[ExperimentMode.BASELINE])
        // derived["repetitions"]
    )
    assert all(isinstance(json.loads(row["metrics"]), dict) for row in csv_rows)


def test_official_safety_assets_identify_the_exact_run() -> None:
    derived = json.loads(DERIVED_JSON.read_text(encoding="utf-8"))
    run_id = derived["run_ids"][0]
    commit = derived["git_commits"][0]
    run_date = derived["run_dates"][0]
    chart = ROOT / "docs" / "report-v2" / "assets" / "v2-security-comparison.svg"
    card = ROOT / "docs" / "report-v2" / "assets" / "v2-adaptive-approval-card.svg"

    for asset in (chart, card):
        content = asset.read_text(encoding="utf-8")
        assert run_id in content
        assert commit[:12] in content
        assert run_date in content
