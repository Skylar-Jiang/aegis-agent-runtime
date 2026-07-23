"""Offline experiment runner — executes task cases across three modes and records results.

Usage:
    py -3.11 -m uv run --project backend python experiments/runners/run_experiment.py [--mode adaptive_runtime]
    py -3.11 -m uv run --project backend python experiments/runners/run_experiment.py --mode all
"""

import argparse
import asyncio
import csv
import json
import sys
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any

from ra_agent.contracts import ExperimentMode, SourceType, ToolCallRequest
from ra_agent.core.bootstrap import build_mock_container, build_runtime_scheduler
from ra_agent.core.ids import new_id

RESULTS_DIR = Path(__file__).parent.parent / "results"
CASES_DIR = Path(__file__).parent.parent / "cases"


def _container_for_mode(mode: ExperimentMode) -> Any:
    """Build a ServiceContainer for the given experiment mode.

    Phase 3 note: BASELINE and FULL_GUARD currently use the mock container.
    Real mode-switching logic (bypassing security for BASELINE, full checks
    for FULL_GUARD) depends on Member A's Runtime bootstrap and Member B's
    real Checker implementations.  For now all three modes exercise the
    same mock pipeline with the mode recorded as metadata.
    """
    return build_mock_container()


async def run_case(case: dict, mode: ExperimentMode) -> dict:
    container = _container_for_mode(mode)
    scheduler = build_runtime_scheduler(container)

    task_id = new_id("exp-task")
    step_id = new_id("step")
    request_id = new_id("req")
    started_at = datetime.now(UTC)

    request = ToolCallRequest(
        task_id=task_id,
        step_id=step_id,
        request_id=request_id,
        tool_name=case["tool_name"],
        arguments=case.get("arguments", {}),
        objective=case["objective"],
        context_summary=case["description"],
        source_type=SourceType.USER,
        requested_at=started_at,
    )

    try:
        result = await scheduler.schedule(request)
        status = result.status.value
        error = result.error
        error_code = result.error_code
        output = str(result.output)[:200] if result.output else None
    except Exception as exc:
        status = "ERROR"
        error = str(exc)
        error_code = type(exc).__name__
        output = None

    finished_at = datetime.now(UTC)
    elapsed_ms = int((finished_at - started_at).total_seconds() * 1000)

    return {
        "case_id": case["case_id"],
        "mode": mode.value,
        "task_id": task_id,
        "request_id": request_id,
        "status": status,
        "expected_decision": case.get("expected_decision", ""),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "elapsed_ms": elapsed_ms,
        "result_output": output,
        "error": error,
        "error_code": error_code,
    }


async def run_all_cases(mode: ExperimentMode) -> list[dict]:
    cases_path = CASES_DIR / "task_cases.json"
    cases = json.loads(cases_path.read_text())
    results: list[dict] = []
    for case in cases:
        result = await run_case(case, mode)
        results.append(result)
        icon = "✗" if result.get("error") else "✓"
        print(f"  {icon} [{mode.value}] {case['case_id']}: {result['status']}")
    return results


def save_json(results: list[dict], stem: str) -> Path:
    path = RESULTS_DIR / f"{stem}.json"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    return path


def save_csv(results: list[dict], stem: str) -> Path:
    path = RESULTS_DIR / f"{stem}.csv"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if not results:
        path.write_text("")
        return path
    keys = [
        "case_id", "mode", "status", "expected_decision", "elapsed_ms",
        "error", "error_code", "started_at",
    ]
    with StringIO(newline="") as buf:
        writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
        path.write_text(buf.getvalue())
    return path


def save_markdown(results: list[dict], stem: str) -> Path:
    path = RESULTS_DIR / f"{stem}.md"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    if not results:
        path.write_text("# Experiment Results\n\n*No results.*\n")
        return path

    lines = [
        f"# Experiment Results — {stem}",
        "",
        f"**Executed at:** {datetime.now(UTC).isoformat()}",
        f"**Total cases:** {len(results)}",
        "",
        "| Case | Mode | Status | Expected | Elapsed (ms) | Error |",
        "|------|------|--------|----------|-------------|-------|",
    ]
    for r in results:
        err = (r.get("error") or "")[:40]
        lines.append(
            f"| {r['case_id']} | {r['mode']} | {r['status']} "
            f"| {r.get('expected_decision', '')} | {r.get('elapsed_ms', '')} | {err} |"
        )

    # Summary
    total = len(results)
    errors = sum(1 for r in results if r.get("error"))
    lines.extend([
        "",
        "## Summary",
        "",
        f"- **Total:** {total}",
        f"- **Errors:** {errors}",
        f"- **Success rate:** {(total - errors) / total * 100:.1f}%" if total > 0 else "- **Success rate:** N/A",
    ])

    path.write_text("\n".join(lines) + "\n")
    return path


async def main() -> None:
    parser = argparse.ArgumentParser(description="RA-Agent Experiment Runner")
    parser.add_argument(
        "--mode",
        choices=["baseline", "full_guard", "adaptive_runtime", "all"],
        default="adaptive_runtime",
        help="Experiment mode (default: adaptive_runtime)",
    )
    args = parser.parse_args()

    modes: list[ExperimentMode]
    if args.mode == "all":
        modes = list(ExperimentMode)
    else:
        mode_map = {
            "baseline": ExperimentMode.BASELINE,
            "full_guard": ExperimentMode.FULL_GUARD,
            "adaptive_runtime": ExperimentMode.ADAPTIVE_RUNTIME,
        }
        modes = [mode_map[args.mode]]

    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    all_results: list[dict] = []

    for mode in modes:
        print(f"\n=== {mode.value} mode ===")
        results = await run_all_cases(mode)
        all_results.extend(results)

        stem = f"{mode.value.lower()}_{timestamp}"
        json_path = save_json(results, stem)
        csv_path = save_csv(results, stem)
        md_path = save_markdown(results, stem)
        print(f"  JSON : {json_path}")
        print(f"  CSV  : {csv_path}")
        print(f"  MD   : {md_path}")

    if len(modes) > 1:
        stem = f"comparison_{timestamp}"
        save_markdown(all_results, stem)
        print(f"\nComparison report: {RESULTS_DIR / stem}.md")

    print(f"\nTotal: {len(all_results)} results across {len(modes)} mode(s)")


if __name__ == "__main__":
    asyncio.run(main())
