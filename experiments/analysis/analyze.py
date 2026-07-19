"""Analysis utilities for experiment result comparison."""

import json
from pathlib import Path


def load_latest_results(results_dir: Path | None = None) -> list[dict]:
    if results_dir is None:
        results_dir = Path(__file__).parent.parent / "results"
    files = sorted(results_dir.glob("run_*.json"), reverse=True)
    if not files:
        return []
    return json.loads(files[0].read_text())


def summarize(results: list[dict]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r.get("error") is None and r.get("error_code") is None)
    return {
        "total_cases": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": f"{passed / total * 100:.1f}%" if total > 0 else "N/A",
        "details": [
            {
                "case_id": r["case_id"],
                "status": r["status"],
                "expected": r["expected_decision"],
                "error": r.get("error"),
            }
            for r in results
        ],
    }
