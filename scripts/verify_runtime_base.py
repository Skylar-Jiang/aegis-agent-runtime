from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

CASES = (
    (
        "B1_AGENT_APPROVAL_RESUME",
        "Approval API resumes a paused Agent task and reaches completion",
        "tests/integration/test_task_runner_api.py::test_agent_task_approval_endpoint_resumes_and_completes_the_task",
    ),
    (
        "B2_TASK_API_FACTS",
        "Task history, steps, and Runtime graph are backed by server state",
        "tests/integration/test_task_runner_api.py::test_tasks_are_listed_and_expose_runtime_steps_and_graph_view",
    ),
    (
        "B3_TASK_PERSISTENCE",
        "Task snapshots survive recreation of the persistence adapter",
        "tests/unit/database/test_task_store.py::test_task_snapshot_survives_store_recreation",
    ),
    (
        "B4_CONTROLLED_WRITE",
        "A real Agent write reaches commit through the Runtime chain",
        "tests/e2e/test_live_runtime.py::test_agent_commits_controlled_write_through_live_runtime",
    ),
    (
        "B5_DANGEROUS_SHELL_BLOCK",
        "A dangerous shell command is blocked before process execution",
        "tests/e2e/test_live_runtime.py::test_agent_blocks_dangerous_shell_before_any_process_runs",
    ),
    (
        "B6_APPROVAL_COMMIT",
        "A granted reversible high-risk operation resumes through controlled commit",
        "tests/integration/test_runtime_approval.py::test_grant_resumes_reversible_high_risk_tool_through_sandbox",
    ),
    (
        "B7_SECURITY_PROFILE_PERSISTENCE",
        "Versioned permissions and conversations survive a Runtime restart",
        "tests/integration/test_workbench_api.py::test_profile_and_conversation_survive_a_persistent_runtime_restart",
    ),
    (
        "B8_CONVERSATION_CONTEXT",
        "A follow-up TaskRun receives prior user and Agent messages",
        "tests/integration/test_workbench_api.py::test_follow_up_turn_receives_prior_user_and_assistant_messages",
    ),
    (
        "B9_TOOL_SCHEMAS",
        "Every production tool exposes and enforces its Runtime-owned JSON Schema",
        "tests/unit/tools/test_tool_schemas.py::test_every_runtime_tool_exposes_and_enforces_one_json_schema",
    ),
    (
        "B10_CONVERSATION_RUNTIME_WRITE",
        "A Conversation TaskRun commits a real file through checkpoint and deep check",
        "tests/integration/test_workbench_api.py::test_conversation_agent_write_uses_the_real_runtime_chain",
    ),
    (
        "B11_PROFILE_APPROVAL_RESUME",
        "A Profile approval rule pauses side effects and resumes the same TaskRun",
        "tests/integration/test_workbench_api.py::test_profile_approval_rule_waits_then_resumes_the_same_conversation_task",
    ),
)


def run_case(case_id: str, description: str, node_id: str) -> dict[str, object]:
    started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", node_id],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    output = output.replace(str(ROOT), "<PROJECT_ROOT>")
    return {
        "case_id": case_id,
        "description": description,
        "pytest_node": node_id,
        "status": "PASS" if completed.returncode == 0 else "FAIL",
        "return_code": completed.returncode,
        "elapsed_ms": elapsed_ms,
        "output_tail": output[-2000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run deterministic Aegis Runtime Base acceptance experiments."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / ".runtime" / "base-validation.json",
    )
    args = parser.parse_args()

    results = []
    for case_id, description, node_id in CASES:
        print(f"[{case_id}] {description}", flush=True)
        result = run_case(case_id, description, node_id)
        results.append(result)
        print(f"  -> {result['status']} ({result['elapsed_ms']} ms)", flush=True)

    passed = sum(result["status"] == "PASS" for result in results)
    report = {
        "suite": "Aegis Runtime Base main-chain validation",
        "generated_at": datetime.now(UTC).isoformat(),
        "python": sys.version,
        "deterministic": True,
        "external_llm_required": False,
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "status": "PASS" if passed == len(results) else "FAIL",
        },
        "cases": results,
    }
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Report: {output_path}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
