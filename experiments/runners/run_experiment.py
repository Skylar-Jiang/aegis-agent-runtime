"""Offline experiment runner — executes task cases and records results."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from ra_agent.core.bootstrap import build_mock_container, build_runtime_scheduler
from ra_agent.contracts import SourceType, ToolCallRequest
from ra_agent.core.ids import new_id


async def run_case(case: dict) -> dict:
    container = build_mock_container()
    scheduler = build_runtime_scheduler(container)

    task_id = new_id("exp-task")
    step_id = new_id("step")
    request_id = new_id("req")

    request = ToolCallRequest(
        task_id=task_id,
        step_id=step_id,
        request_id=request_id,
        tool_name=case["tool_name"],
        arguments=case["arguments"],
        objective=case["objective"],
        context_summary=case["description"],
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
    )

    result = await scheduler.schedule(request)

    return {
        "case_id": case["case_id"],
        "task_id": task_id,
        "request_id": request_id,
        "status": result.status.value,
        "expected_decision": case["expected_decision"],
        "timestamp": datetime.now(UTC).isoformat(),
        "result_output": str(result.output)[:200] if result.output else None,
        "error": result.error,
        "error_code": result.error_code,
    }


async def main() -> None:
    cases_path = Path(__file__).parent.parent / "cases" / "task_cases.json"
    cases = json.loads(cases_path.read_text())
    results = []

    for case in cases:
        result = await run_case(case)
        results.append(result)
        status_icon = "✓" if "error" not in result or not result["error"] else "✗"
        print(f"  {status_icon} {case['case_id']}: {result['status']}")

    out_path = Path(__file__).parent.parent / "results" / f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
