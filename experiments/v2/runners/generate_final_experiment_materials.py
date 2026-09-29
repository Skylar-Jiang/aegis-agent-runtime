"""Generate unified final experiment tables and figures from frozen raw files."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

MODES = ("RUNTIME_OFF", "APPROVAL_ONLY", "AEGIS_RUNTIME_ON")
COLORS = {
    "RUNTIME_OFF": "#64748b",
    "APPROVAL_ONLY": "#b45309",
    "AEGIS_RUNTIME_ON": "#0f766e",
}
LABELS = {
    "RUNTIME_OFF": "Runtime OFF",
    "APPROVAL_ONLY": "Approval-only",
    "AEGIS_RUNTIME_ON": "Aegis Runtime",
}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = sorted({field for row in rows for field in row})
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            {
                key: json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else value
                for key, value in row.items()
            }
            for row in rows
        )


def _save(figure, figures: Path, stem: str) -> None:
    figure.tight_layout()
    for suffix in ("svg", "pdf", "png"):
        figure.savefig(
            figures / f"{stem}.{suffix}",
            dpi=360 if suffix == "png" else None,
            bbox_inches="tight",
        )
    plt.close(figure)


def _workflow(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, int, float], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["mode"], row["node_count"], row["high_risk_fraction"])].append(row)
    summary = []
    for (mode, node_count, fraction), group in sorted(grouped.items()):
        summary.append(
            {
                "mode": mode,
                "node_count": node_count,
                "high_risk_fraction": fraction,
                "runs": len(group),
                "median_completion_ms": median(
                    row["total_completion_ms"] for row in group
                ),
                "mean_approval_wait_ms": sum(row["approval_wait_ms"] for row in group)
                / len(group),
                "mean_approval_actions": sum(row["approval_actions"] for row in group)
                / len(group),
                "mean_nodes_completed_during_approval": sum(
                    row["nodes_completed_during_approval"] for row in group
                )
                / len(group),
                "mean_safe_parallelism_utilization": sum(
                    row["safe_parallelism_utilization"] for row in group
                )
                / len(group),
                "completion_rate": sum(row["completion"] for row in group) / len(group),
                "conflict_violation_rate": sum(
                    row["conflict_violation_count"] for row in group
                )
                / len(group),
                "dependency_violation_rate": sum(
                    row["dependency_violation_count"] for row in group
                )
                / len(group),
                "unsafe_side_effect_rate": sum(
                    row["unsafe_side_effect_count"] for row in group
                )
                / len(group),
            }
        )
    return summary


def _agent(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    data = [row for row in rows if row.get("evidence_set") == "real_agent_e2e"]
    cases = []
    for case_id in sorted({row["case_id"] for row in data}):
        group = [row for row in data if row["case_id"] == case_id]
        proposals = [row for row in group if row.get("unsafe_proposal_count", 0) > 0]
        cases.append(
            {
                "case_id": case_id,
                "runs": len(group),
                "unsafe_proposals": len(proposals),
                "no_proposal": sum(
                    row.get("containment") == "NO_PROPOSAL" for row in group
                ),
                "contained": sum(
                    row.get("containment") == "CONTAINED" for row in group
                ),
                "unsafe_side_effects": sum(
                    row.get("actual_unsafe_side_effect", False) for row in group
                ),
                "handler_calls": sum(
                    row.get("handler_calls_total", 0) for row in group
                ),
                "completed": sum(
                    row.get("agent_status") == "COMPLETED" for row in group
                ),
                "audit_complete": sum(
                    row.get("audit_complete", False) for row in group
                ),
                "model": group[0].get("model"),
            }
        )
    unsafe = [row for row in data if row.get("category") == "unsafe"]
    proposals = [row for row in unsafe if row.get("unsafe_proposal_count", 0) > 0]
    benign = [row for row in data if row.get("category") == "benign"]
    return cases, {
        "model": data[0].get("model") if data else None,
        "unsafe_scenario_runs": len(unsafe),
        "unsafe_toolcall_proposal_rate": len(proposals) / len(unsafe)
        if unsafe
        else None,
        "conditional_runtime_containment_rate": sum(
            row.get("containment") == "CONTAINED" for row in proposals
        )
        / len(proposals)
        if proposals
        else None,
        "actual_unsafe_side_effect_rate": sum(
            row.get("actual_unsafe_side_effect", False) for row in unsafe
        )
        / len(unsafe)
        if unsafe
        else None,
        "benign_completion_rate": sum(
            row.get("agent_status") == "COMPLETED" for row in benign
        )
        / len(benign)
        if benign
        else None,
        "audit_completeness": sum(row.get("audit_complete", False) for row in data)
        / len(data)
        if data
        else None,
    }


def _ablation(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for mode in MODES:
        safety = [
            row
            for row in rows
            if row.get("evidence_set") == "safety" and row.get("mode") == mode
        ]
        scheduling = [
            row
            for row in rows
            if row.get("evidence_set") == "scheduling" and row.get("mode") == mode
        ]
        recovery = next(
            row
            for row in rows
            if row.get("evidence_set") == "recovery"
            and row.get("mode") == mode
            and row.get("case_id") == "dependency_closure"
        )
        recovery_available = mode != "RUNTIME_OFF"
        result[mode] = {
            "unsafe_side_effect_rate": sum(
                row["actual_unsafe_side_effect"] for row in safety
            )
            / len(safety),
            "containment_rate": sum(row["contained_before_handler"] for row in safety)
            / len(safety),
            "safe_parallelism": sum(
                row["peak_concurrency"]
                for row in scheduling
                if row["case_id"] == "independent_writes"
            )
            / sum(row["case_id"] == "independent_writes" for row in scheduling),
            "completion_rate": sum(row["completion"] for row in scheduling)
            / len(scheduling),
            "approval_actions_per_task": sum(
                row["approval_actions"] for row in scheduling
            )
            / len(scheduling),
            "rollback_scope": recovery["rollback_scope_size"]
            if recovery_available
            else None,
            "preservation_rate": float(recovery["preservation_correct"])
            if recovery_available
            else None,
            "recovery_precision": 2 / recovery["rollback_scope_size"]
            if recovery_available and recovery["rollback_scope_size"]
            else None,
        }
    return result


def _matrix(ablation: dict[str, dict[str, Any]], derived: Path) -> list[dict[str, Any]]:
    rows = []
    for metric in (
        "unsafe_side_effect_rate",
        "containment_rate",
        "safe_parallelism",
        "completion_rate",
        "approval_actions_per_task",
        "rollback_scope",
        "preservation_rate",
        "recovery_precision",
    ):
        rows.append(
            {
                "metric": metric,
                **{LABELS[mode]: ablation[mode][metric] for mode in MODES},
            }
        )
    _write_csv(derived / "final-three-mode-metric-matrix.csv", rows)
    return rows


def _figures(
    workflow: list[dict[str, Any]],
    agent_cases: list[dict[str, Any]],
    agent_summary: dict[str, Any],
    ablation: dict[str, dict[str, Any]],
    v5_conflicts: list[dict[str, Any]],
    figures: Path,
) -> None:
    figure, axes = plt.subplots(1, 2, figsize=(11.7, 4.2))
    for fraction, axis in zip((0.2, 0.4), axes, strict=True):
        for mode in ("BASELINE", "FULL_GUARD", "ADAPTIVE_RUNTIME"):
            values = [
                row
                for row in workflow
                if row["mode"] == mode and row["high_risk_fraction"] == fraction
            ]
            axis.plot(
                [row["node_count"] for row in values],
                [row["median_completion_ms"] for row in values],
                marker="o",
                label={
                    "BASELINE": "Runtime OFF",
                    "FULL_GUARD": "Approval-only",
                    "ADAPTIVE_RUNTIME": "Aegis Runtime",
                }[mode],
                color={
                    "BASELINE": "#64748b",
                    "FULL_GUARD": "#b45309",
                    "ADAPTIVE_RUNTIME": "#0f766e",
                }[mode],
            )
        axis.set_title(f"high-risk delete fraction = {fraction}")
        axis.set_xlabel("Workflow nodes")
        axis.set_ylabel("Median completion (ms)")
        axis.grid(alpha=0.2)
    axes[1].legend(frameon=False, fontsize=8)
    figure.suptitle("Long-horizon approval-aware workflow scaling")
    _save(figure, figures, "final-long-workflow-completion")

    figure, axes = plt.subplots(1, 2, figsize=(11.7, 4.2))
    for fraction, axis in zip((0.2, 0.4), axes, strict=True):
        for mode in ("FULL_GUARD", "ADAPTIVE_RUNTIME"):
            values = [
                row
                for row in workflow
                if row["mode"] == mode and row["high_risk_fraction"] == fraction
            ]
            axis.plot(
                [row["node_count"] for row in values],
                [row["mean_approval_actions"] for row in values],
                marker="o",
                label={
                    "FULL_GUARD": "Approval-only",
                    "ADAPTIVE_RUNTIME": "Aegis Runtime",
                }[mode],
                color={"FULL_GUARD": "#b45309", "ADAPTIVE_RUNTIME": "#0f766e"}[mode],
            )
        axis.set_title(f"high-risk fraction = {fraction}")
        axis.set_xlabel("Workflow nodes")
        axis.set_ylabel("Mean approval actions")
        axis.grid(alpha=0.2)
    axes[1].legend(frameon=False, fontsize=8)
    figure.suptitle("Approval workload: same workflow, fewer manual actions")
    _save(figure, figures, "final-approval-workload")

    figure, axis = plt.subplots(figsize=(11.2, 4.1))
    axis.axis("off")
    capability = [
        ["Risk analysis", "—", "—", "yes"],
        ["Effect Target", "—", "—", "yes"],
        ["Conflict scheduling", "—", "—", "yes"],
        ["Approval routing", "—", "manual", "policy"],
        ["Effect lineage", "—", "—", "yes"],
        ["Selective recovery", "—", "full", "closure"],
    ]
    table = axis.table(
        cellText=capability,
        colLabels=["Capability", "Runtime OFF", "Approval-only", "Aegis"],
        cellLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.6)
    axis.set_title("Final three-mode capability matrix", pad=15)
    _save(figure, figures, "final-capability-matrix")

    figure, axes = plt.subplots(1, 2, figsize=(11.7, 4.1))
    positions = list(range(3))
    axes[0].bar(
        [position - 0.18 for position in positions],
        [ablation[mode]["containment_rate"] for mode in MODES],
        width=0.36,
        color="#0f766e",
        label="Containment rate",
    )
    axes[0].bar(
        [position + 0.18 for position in positions],
        [ablation[mode]["unsafe_side_effect_rate"] for mode in MODES],
        width=0.36,
        color="#b91c1c",
        label="Unsafe side-effect rate",
    )
    axes[0].set_xticks(
        positions, [LABELS[mode] for mode in MODES], rotation=12, ha="right"
    )
    axes[0].set_ylim(0, 1.15)
    axes[0].set_title("Deterministic safety ablation")
    axes[0].legend(frameon=False, fontsize=8)
    axes[1].bar(
        positions,
        [ablation[mode]["safe_parallelism"] for mode in MODES],
        color=[COLORS[mode] for mode in MODES],
    )
    axes[1].set_xticks(
        positions, [LABELS[mode] for mode in MODES], rotation=12, ha="right"
    )
    axes[1].set_ylim(0, 2.2)
    axes[1].set_title("Safe independent parallelism")
    _save(figure, figures, "final-safety-scheduling")

    figure, axes = plt.subplots(1, 2, figsize=(11.7, 4.1))
    for axis, metric, title, ylabel, upper in (
        (axes[0], "rollback_scope", "Rollback scope", "Effects selected", 3.6),
        (axes[1], "preservation_rate", "Independent-effect preservation", "Rate", 1.2),
    ):
        values = [ablation[mode][metric] for mode in MODES]
        axis.bar(
            positions,
            [value if value is not None else 0 for value in values],
            color=[
                COLORS[mode] if value is not None else "#cbd5e1"
                for mode, value in zip(MODES, values, strict=True)
            ],
        )
        for position, value in zip(positions, values, strict=True):
            if value is None:
                axis.text(
                    position,
                    upper * 0.08,
                    "N/A\n(no recovery)",
                    ha="center",
                    va="bottom",
                    fontsize=8,
                )
        axis.set_xticks(
            positions, [LABELS[mode] for mode in MODES], rotation=12, ha="right"
        )
        axis.set_ylim(0, upper)
        axis.set_title(title)
        axis.set_ylabel(ylabel)
    _save(figure, figures, "final-recovery")

    figure, axis = plt.subplots(figsize=(11.7, 4.3))
    axis.axis("off")
    values = [
        [
            row["case_id"].replace("_", "\n"),
            row["runs"],
            row["unsafe_proposals"],
            row["contained"],
            row["no_proposal"],
            row["unsafe_side_effects"],
            row["completed"],
        ]
        for row in agent_cases
    ]
    table = axis.table(
        cellText=values,
        colLabels=[
            "Live scenario",
            "Runs",
            "Unsafe proposals",
            "Contained",
            "NO_PROPOSAL",
            "Unsafe effects",
            "Completed",
        ],
        cellLoc="center",
        loc="center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 1.9)
    axis.set_title(
        f"Repeated real-Agent matrix ({agent_summary['model']}; NO_PROPOSAL excluded from containment)",
        pad=15,
    )
    _save(figure, figures, "final-real-agent-matrix")

    _write_csv(
        figures.parent / "derived" / "final-v5-conflict-coverage.csv", v5_conflicts
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow-raw", required=True, type=Path)
    parser.add_argument("--agent-raw", required=True, type=Path)
    parser.add_argument("--ablation-raw", required=True, type=Path)
    parser.add_argument("--v5-raw", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    args = parser.parse_args()
    output, derived, figures = (
        args.output_directory,
        args.output_directory / "derived",
        args.output_directory / "figures",
    )
    output.mkdir(parents=True, exist_ok=True)
    derived.mkdir(parents=True, exist_ok=True)
    figures.mkdir(parents=True, exist_ok=True)
    workflow = _workflow(_read_jsonl(args.workflow_raw))
    agent_cases, agent_summary = _agent(_read_jsonl(args.agent_raw))
    ablation = _ablation(_read_jsonl(args.ablation_raw))
    v5_conflicts = [
        row
        for row in _read_jsonl(args.v5_raw)
        if row.get("evidence_set") == "effect_target_conflict"
    ]
    _write_csv(derived / "final-long-workflow-summary.csv", workflow)
    _write_csv(derived / "final-real-agent-cases.csv", agent_cases)
    (derived / "final-real-agent-summary.json").write_text(
        json.dumps(agent_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    matrix = _matrix(ablation, derived)
    (derived / "final-manifest.json").write_text(
        json.dumps(
            {
                "workflow_raw": str(args.workflow_raw),
                "agent_raw": str(args.agent_raw),
                "ablation_raw": str(args.ablation_raw),
                "v5_raw": str(args.v5_raw),
                "metric_rows": len(matrix),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _figures(workflow, agent_cases, agent_summary, ablation, v5_conflicts, figures)
    print(f"wrote final derived material to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
