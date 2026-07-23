"""Analysis utilities for experiment result comparison.

Supports three-mode comparison: BASELINE, FULL_GUARD, ADAPTIVE_RUNTIME.
"""

import json
from pathlib import Path
from typing import Any


def load_results(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def load_latest_results(prefix: str = "", results_dir: Path | None = None) -> list[dict]:
    if results_dir is None:
        results_dir = Path(__file__).parent.parent / "results"
    pattern = f"{prefix}*.json" if prefix else "*.json"
    files = sorted(results_dir.glob(pattern), reverse=True)
    if not files:
        return []
    return load_results(files[0])


def compare_modes(results: list[dict]) -> dict[str, Any]:
    """Compare metrics across experiment modes."""
    by_mode: dict[str, list[dict]] = {}
    for r in results:
        mode = r.get("mode", "unknown")
        by_mode.setdefault(mode, []).append(r)

    comparison: dict[str, Any] = {"modes": {}, "cross_mode": {}}

    for mode, entries in by_mode.items():
        total = len(entries)
        errors = sum(1 for e in entries if e.get("error"))
        blocked = sum(1 for e in entries if e.get("status") == "BLOCKED")
        avg_elapsed = (
            sum(e.get("elapsed_ms", 0) for e in entries) / total
            if total > 0 else 0
        )
        comparison["modes"][mode] = {
            "total": total,
            "errors": errors,
            "blocked": blocked,
            "success_rate": (
                f"{(total - errors) / total * 100:.1f}%"
                if total > 0 else "N/A"
            ),
            "block_rate": (
                f"{blocked / total * 100:.1f}%"
                if total > 0 else "N/A"
            ),
            "avg_elapsed_ms": round(avg_elapsed),
        }

    mode_keys = sorted(by_mode.keys())
    if len(mode_keys) >= 2:
        comparison["cross_mode"] = {
            "mode_order": mode_keys,
            "total_results": len(results),
        }

    return comparison


def summarize(results: list[dict]) -> dict[str, Any]:
    total = len(results)
    passed = sum(
        1 for r in results if r.get("error") is None and r.get("error_code") is None
    )
    return {
        "total_cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": f"{passed / total * 100:.1f}%" if total > 0 else "N/A",
        "details": [
            {
                "case_id": r["case_id"],
                "mode": r.get("mode", ""),
                "status": r["status"],
                "expected": r.get("expected_decision", ""),
                "error": r.get("error"),
                "elapsed_ms": r.get("elapsed_ms"),
            }
            for r in results
        ],
    }
