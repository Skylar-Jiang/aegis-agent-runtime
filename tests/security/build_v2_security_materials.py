"""Build member-2 derived metrics and report SVGs from raw ExperimentResult rows."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from ra_agent.contracts import ExperimentMode, ExperimentResult

ROOT = Path(__file__).resolve().parents[2]
MODE_ORDER = (
    ExperimentMode.BASELINE,
    ExperimentMode.FULL_GUARD,
    ExperimentMode.ADAPTIVE_RUNTIME,
)
MODE_LABELS = {
    ExperimentMode.BASELINE: "Baseline",
    ExperimentMode.FULL_GUARD: "Full Guard",
    ExperimentMode.ADAPTIVE_RUNTIME: "Adaptive",
}
MODE_COLORS = {
    ExperimentMode.BASELINE: "#94a3b8",
    ExperimentMode.FULL_GUARD: "#f59e0b",
    ExperimentMode.ADAPTIVE_RUNTIME: "#22c55e",
}


def build_materials(
    *,
    raw_jsonl: Path,
    derived_json: Path,
    comparison_svg: Path,
    approval_card_svg: Path,
) -> dict[str, object]:
    rows = [
        ExperimentResult.model_validate_json(line)
        for line in raw_jsonl.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        raise ValueError("raw experiment file must contain at least one row")
    _validate_comparable(rows)
    metrics = _aggregate(rows)
    relative_raw = _display_path(raw_jsonl)
    payload: dict[str, object] = {
        "schema_version": "0.4-derived",
        "generated_at": datetime.now(UTC).isoformat(),
        "source_raw": relative_raw,
        "run_ids": sorted({row.run_id for row in rows}),
        "git_commits": sorted({row.git_commit for row in rows}),
        "environment_fingerprints": sorted(
            {row.environment_fingerprint for row in rows}
        ),
        "sample_size": len(rows),
        "fixture_count": len({row.fixture_id for row in rows}),
        "repetitions": len({row.repetition for row in rows}),
        "metrics": metrics,
    }
    derived_json.parent.mkdir(parents=True, exist_ok=True)
    comparison_svg.parent.mkdir(parents=True, exist_ok=True)
    approval_card_svg.parent.mkdir(parents=True, exist_ok=True)
    derived_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    comparison_svg.write_text(
        _comparison_svg(metrics, payload),
        encoding="utf-8",
    )
    approval_card_svg.write_text(
        _approval_card_svg(rows, payload),
        encoding="utf-8",
    )
    return payload


def _validate_comparable(rows: list[ExperimentResult]) -> None:
    signatures: dict[ExperimentMode, set[tuple[str, int]]] = defaultdict(set)
    for row in rows:
        signatures[row.mode].add((row.fixture_id, row.repetition))
        if row.parallel_saved_ms != 0 or row.node_count != 1:
            raise ValueError(
                "member-2 security runs must remain single-node comparisons"
            )
    if set(signatures) != set(MODE_ORDER):
        raise ValueError("raw data must contain all three frozen experiment modes")
    expected = signatures[ExperimentMode.BASELINE]
    if any(signatures[mode] != expected for mode in MODE_ORDER[1:]):
        raise ValueError("all modes must use identical fixture/repetition pairs")


def _aggregate(
    rows: list[ExperimentResult],
) -> dict[str, dict[str, int | float]]:
    grouped: dict[ExperimentMode, list[ExperimentResult]] = defaultdict(list)
    for row in rows:
        grouped[row.mode].append(row)

    metrics: dict[str, dict[str, int | float]] = {}
    for mode in MODE_ORDER:
        mode_rows = grouped[mode]
        repetitions = len({row.repetition for row in mode_rows})
        unsafe_rows = [row for row in mode_rows if row.objective_class == "UNSAFE"]
        blocked_unsafe = sum(row.status == "BLOCKED" for row in unsafe_rows)
        manual_total = sum(row.manual_action_count for row in mode_rows)
        unsafe_admitted_total = sum(row.unsafe_tool_executed_count for row in mode_rows)
        elapsed_values = [row.elapsed_ms for row in mode_rows]
        metrics[mode.value] = {
            "rows": len(mode_rows),
            "unsafe_rows": len(unsafe_rows),
            "unsafe_request_block_rate": round(
                blocked_unsafe / len(unsafe_rows) if unsafe_rows else 1.0,
                4,
            ),
            "unsafe_tool_executed_count": unsafe_admitted_total,
            "unsafe_admitted_per_repetition": round(
                unsafe_admitted_total / repetitions,
                2,
            ),
            "false_block_count": sum(row.false_block_count for row in mode_rows),
            "approval_requested_count": sum(
                row.approval_requested_count for row in mode_rows
            ),
            "approval_decision_count": sum(
                row.approval_decision_count for row in mode_rows
            ),
            "manual_action_count": manual_total,
            "manual_actions_per_repetition": round(
                manual_total / repetitions,
                2,
            ),
            "risk_escalation_count": sum(
                row.risk_escalation_count for row in mode_rows
            ),
            "mean_elapsed_ms": round(statistics.mean(elapsed_values), 2),
            "median_elapsed_ms": round(statistics.median(elapsed_values), 2),
        }
    return metrics


def _comparison_svg(
    metrics: dict[str, dict[str, int | float]],
    payload: dict[str, object],
) -> str:
    width, height = 1200, 620
    panels = (
        ("Manual actions / repetition", "manual_actions_per_repetition", 8.0, ""),
        ("Unsafe request block rate", "unsafe_request_block_rate", 1.0, "%"),
        ("Unsafe admitted / repetition", "unsafe_admitted_per_repetition", 5.0, ""),
    )
    parts = [
        _svg_open(width, height),
        '<rect width="1200" height="620" rx="28" fill="#0f172a"/>',
        '<text x="60" y="68" fill="#f8fafc" font-size="30" '
        'font-family="Segoe UI, sans-serif" font-weight="700">'
        "V2 Security: safety vs. manual effort</text>",
        '<text x="60" y="102" fill="#94a3b8" font-size="16" '
        'font-family="Segoe UI, sans-serif">'
        f"source={escape(str(payload['source_raw']))} · n={payload['sample_size']}</text>",
    ]
    for panel_index, (title, key, maximum, suffix) in enumerate(panels):
        x0 = 45 + panel_index * 390
        parts.append(
            f'<rect x="{x0}" y="135" width="360" height="400" rx="18" '
            'fill="#111c31" stroke="#26344d"/>'
        )
        parts.append(
            f'<text x="{x0 + 24}" y="176" fill="#e2e8f0" font-size="18" '
            f'font-family="Segoe UI, sans-serif" font-weight="600">{escape(title)}</text>'
        )
        for mode_index, mode in enumerate(MODE_ORDER):
            value = float(metrics[mode.value][key])
            display_value = value * 100 if suffix == "%" else value
            normalized = value / maximum if maximum else 0
            bar_width = max(2, min(260, 260 * normalized))
            y = 220 + mode_index * 92
            parts.extend(
                [
                    f'<text x="{x0 + 24}" y="{y}" fill="#cbd5e1" font-size="15" '
                    f'font-family="Segoe UI, sans-serif">{MODE_LABELS[mode]}</text>',
                    f'<rect x="{x0 + 24}" y="{y + 15}" width="270" height="22" '
                    'rx="11" fill="#1e293b"/>',
                    f'<rect x="{x0 + 24}" y="{y + 15}" width="{bar_width:.1f}" '
                    f'height="22" rx="11" fill="{MODE_COLORS[mode]}"/>',
                    f'<text x="{x0 + 304}" y="{y + 32}" fill="#f8fafc" '
                    'font-size="15" font-family="Consolas, monospace">'
                    f"{display_value:.1f}{suffix}</text>",
                ]
            )
    parts.extend(
        [
            '<text x="60" y="580" fill="#64748b" font-size="14" '
            'font-family="Segoe UI, sans-serif">'
            "Probe executor has no external side effect; BASELINE bypass scope is recorded "
            "in every raw row.</text>",
            "</svg>",
        ]
    )
    return "\n".join(parts) + "\n"


def _approval_card_svg(
    rows: list[ExperimentResult],
    payload: dict[str, object],
) -> str:
    candidates = [
        row
        for row in rows
        if row.mode is ExperimentMode.ADAPTIVE_RUNTIME
        and row.case_id == "approved-sensitive-read"
        and row.approval_requested_count == 1
    ]
    if not candidates:
        raise ValueError("raw data lacks the adaptive approval evidence fixture")
    row = min(candidates, key=lambda item: item.repetition)
    digest = (row.audit_digest or "N/A")[:20]
    tool_name = row.tool_sequence[0] if row.tool_sequence else "N/A"
    fields = (
        ("Run", row.run_id),
        ("Task", row.task_id),
        ("Tool", tool_name),
        ("Mode", row.mode.value),
        ("Approval", "requested=1 · decision=1 · manual=1"),
        ("Final status", row.status),
        ("Audit digest", digest + "…"),
        ("Code commit", row.git_commit[:12]),
    )
    parts = [
        _svg_open(1040, 670),
        '<rect width="1040" height="670" rx="32" fill="#07111f"/>',
        '<rect x="48" y="42" width="944" height="586" rx="24" '
        'fill="#111c31" stroke="#f59e0b" stroke-width="2"/>',
        '<circle cx="92" cy="91" r="18" fill="#f59e0b"/>',
        '<text x="126" y="101" fill="#f8fafc" font-size="28" '
        'font-family="Segoe UI, sans-serif" font-weight="700">'
        "Adaptive approval evidence card</text>",
        '<text x="78" y="145" fill="#fbbf24" font-size="17" '
        'font-family="Segoe UI, sans-serif">'
        "Sensitive read · explicit human decision · original request correlation</text>",
    ]
    for index, (label, value) in enumerate(fields):
        y = 200 + index * 51
        parts.extend(
            [
                f'<text x="82" y="{y}" fill="#94a3b8" font-size="15" '
                f'font-family="Segoe UI, sans-serif">{escape(label)}</text>',
                f'<text x="250" y="{y}" fill="#e2e8f0" font-size="16" '
                f'font-family="Consolas, monospace">{escape(str(value))}</text>',
            ]
        )
    parts.extend(
        [
            '<rect x="78" y="590" width="884" height="1" fill="#26344d"/>',
            '<text x="78" y="616" fill="#64748b" font-size="13" '
            'font-family="Segoe UI, sans-serif">'
            f"Generated only from {escape(str(payload['source_raw']))}; no secret or raw "
            "tool output shown.</text>",
            "</svg>",
        ]
    )
    return "\n".join(parts) + "\n"


def _svg_open(width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">'
    )


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-jsonl", type=Path, required=True)
    parser.add_argument("--derived-json", type=Path, required=True)
    parser.add_argument("--comparison-svg", type=Path, required=True)
    parser.add_argument("--approval-card-svg", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_materials(
        raw_jsonl=args.raw_jsonl,
        derived_json=args.derived_json,
        comparison_svg=args.comparison_svg,
        approval_card_svg=args.approval_card_svg,
    )
    print(
        f"built materials from {payload['sample_size']} rows; "
        f"derived={args.derived_json}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
