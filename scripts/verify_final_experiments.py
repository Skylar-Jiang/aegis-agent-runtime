"""Verify fixed formal V2 experiment artifacts without altering them."""

from __future__ import annotations

import argparse
import ast
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "experiments" / "v2" / "results" / "raw"
DERIVED = ROOT / "experiments" / "v2" / "results" / "derived"
SPECS = {
    "Safety": ("v2-safety-74c071c-20260801", 225, 15, 5),
    "Graph": ("v2-graph-7d4d7c2-20260730", 18, 2, 3),
    "Rollback": ("rollback_benchmark", 105, 7, 5),
}


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _csv(path: Path, reference: list[dict[str, object]]) -> list[dict[str, object]]:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
    converted: list[dict[str, object]] = []
    for row, source in zip(rows, reference, strict=True):
        converted.append({key: _convert(value, source[key]) for key, value in row.items()})
    return converted


def _convert(value: str, reference: object) -> object:
    if value == "" and reference is None:
        return None
    if isinstance(reference, (dict, list)):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return ast.literal_eval(value)
    if isinstance(reference, int):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _sorted(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(rows, key=lambda row: (str(row["case_id"]), str(row["mode"]), int(row["repetition"])))


def _verify_family(name: str) -> tuple[list[dict[str, object]], dict[str, object]]:
    stem, expected_rows, expected_fixtures, repetitions = SPECS[name]
    jsonl_path = RAW / f"{stem}.jsonl"
    csv_path = RAW / f"{stem}.csv"
    rows = _jsonl(jsonl_path)
    assert len(rows) == expected_rows, f"{name}: expected {expected_rows} JSONL rows"
    csv_rows = _csv(csv_path, rows)
    assert _sorted(csv_rows) == _sorted(rows), f"{name}: CSV and JSONL differ"
    groups = {(str(row["case_id"]), str(row["mode"])) for row in rows}
    assert len({case for case, _ in groups}) == expected_fixtures, f"{name}: fixture count"
    assert {mode for _, mode in groups} == {"BASELINE", "FULL_GUARD", "ADAPTIVE_RUNTIME"}
    assert all(sum(1 for row in rows if (str(row["case_id"]), str(row["mode"])) == group) == repetitions for group in groups)
    return rows, {"rows": len(rows), "fixtures": expected_fixtures, "repetitions": repetitions, "run_id_count": len({str(row["run_id"]) for row in rows})}


def _verify_graph_derived(rows: list[dict[str, object]]) -> None:
    actual = json.loads((DERIVED / "v2-graph-7d4d7c2-20260730-summary.json").read_text(encoding="utf-8"))
    groups: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["fixture_id"]), str(row["mode"]))].append(row)
    rebuilt = {f"{fixture}:{mode}": {"sample_count": len(group), "mean_graph_elapsed_ms": round(mean(int(row["graph_elapsed_ms"]) for row in group), 2), "mean_parallel_saved_ms": round(mean(int(row["parallel_saved_ms"]) for row in group), 2), "mean_approval_wait_ms": round(mean(int(row["approval_wait_ms"]) for row in group), 2), "mean_nodes_completed_during_approval": round(mean(int(dict(row["metrics"])["nodes_completed_during_approval"]) for row in group), 2)} for (fixture, mode), group in groups.items()}
    assert actual["source_row_count"] == len(rows) and actual["by_fixture_mode"] == rebuilt, "Graph derived mismatch"


def _verify_rollback_derived(rows: list[dict[str, object]]) -> None:
    derived = list(csv.DictReader((DERIVED / "rollback_summary_by_mode.csv").open(encoding="utf-8-sig", newline="")))
    for item in derived:
        group = [row for row in rows if row["mode"] == item["mode"]]
        applicable = [row for row in group if int(row["rollback_count"]) or int(row["selective_rollback_count"])]
        assert int(item["runs"]) == len(group)
        assert int(item["rollback_applicable_runs"]) == len(applicable)
        assert float(item["median_rollback_elapsed_ms"]) == median(int(row["rollback_elapsed_ms"]) for row in applicable)
        assert int(item["total_selective_rollback_count"]) == sum(int(row["selective_rollback_count"]) for row in group)


def verify() -> dict[str, object]:
    safety, safety_report = _verify_family("Safety")
    graph, graph_report = _verify_family("Graph")
    rollback, rollback_report = _verify_family("Rollback")
    _verify_graph_derived(graph)
    _verify_rollback_derived(rollback)
    return {"families": {"Safety": safety_report, "Graph": graph_report, "Rollback": rollback_report}, "command": "py -3.11 scripts/verify_final_experiments.py --report docs/final-integration-experiment-reconstruction.json"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = verify()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
