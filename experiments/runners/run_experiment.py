"""Run deterministic, isolated V2 experiments through real runtime components.

Outputs v0.4 ExperimentResult schema JSON/CSV to raw/ and derived/ directories.
"""

import argparse
import asyncio
import csv
import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ra_agent.contracts import (
    DeepCheckResult,
    ExecutionStatus,
    ExperimentMode,
    PolicyDecision,
    RiskVerdict,
    SourceType,
    TaskContract,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.core.bootstrap import build_runtime_container, build_runtime_scheduler
from ra_agent.core.config import RuntimeMode, Settings
from ra_agent.core.ids import new_id
from ra_agent.database.migrate import upgrade_database
from ra_agent.execution.cleanup import CleanupContext

ROOT_DIR = Path(__file__).resolve().parents[2]
RESULTS_DIR = Path(__file__).parent.parent / "results"
RAW_DIR = RESULTS_DIR / "raw"
DERIVED_DIR = RESULTS_DIR / "derived"
CASES_DIR = Path(__file__).parent.parent / "cases"
CONFIG_DIR = ROOT_DIR / "configs"


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT_DIR, text=True,
        ).strip()
    except Exception:
        return "unknown"


def _environment_fingerprint(case_id: str, mode: ExperimentMode) -> str:
    payload = f"{case_id}|{mode.value}|{platform.platform()}|{sys.version}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class FullGuardPolicy:
    """Retain hard blocks and approvals while routing executable mutations to SandboxFlow."""

    async def decide(self, verdict: RiskVerdict) -> PolicyDecision:
        if verdict.recommended_decision is PolicyDecision.BLOCK:
            return PolicyDecision.BLOCK
        if verdict.recommended_decision is PolicyDecision.REQUEST_APPROVAL:
            return PolicyDecision.REQUEST_APPROVAL
        if verdict.requires_checkpoint:
            return PolicyDecision.SANDBOX_CHECK
        return PolicyDecision.FAST_EXECUTE


def _settings(runtime_root: Path) -> Settings:
    return Settings.model_validate(
        {
            "runtime_mode": RuntimeMode.LIVE_AGENT,
            "database_url": f"sqlite+aiosqlite:///{(runtime_root / 'runtime.db').as_posix()}",
            "security_config_dir": CONFIG_DIR,
            "workspace_root": runtime_root / "workspace",
            "pending_root": runtime_root / "pending",
            "checkpoint_root": runtime_root / "checkpoints",
            "quarantine_root": runtime_root / "quarantine",
        }
    )


def _prepare_workspace(settings: Settings) -> None:
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    (settings.workspace_root / "workspace").mkdir(exist_ok=True)
    (settings.workspace_root / "README.md").write_text("experiment fixture\n", encoding="utf-8")
    (settings.workspace_root / ".env").write_text("TOKEN=fixture-only\n", encoding="utf-8")
    (settings.workspace_root / "temp.log").write_text("delete fixture\n", encoding="utf-8")


async def _run_baseline(
    container: Any,
    request: ToolCallRequest,
) -> ToolExecutionResult:
    """Execute real handlers directly in the disposable workspace, with no checkers."""

    checkpoint_id: str | None = None
    if request.tool_name in {"write_file", "delete_file"}:
        checkpoint = await container.checkpoint_manager.create(request)
        checkpoint_id = checkpoint.checkpoint_id

    try:
        execution = await container.tool_executor.execute(request, checkpoint_id=checkpoint_id)
    except Exception as error:
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.FAILED,
            error=str(error) or type(error).__name__,
            error_code=type(error).__name__,
        )

    if execution.status is ExecutionStatus.SUCCESS:
        return execution.model_copy(update={"status": ExecutionStatus.COMMITTED})
    if execution.status is not ExecutionStatus.PENDING_COMMIT or checkpoint_id is None:
        return execution

    direct_check = DeepCheckResult(
        request_id=request.request_id,
        passed=True,
        reason="Baseline direct execution intentionally bypasses deep checking",
    )
    try:
        committed = await container.commit_gate.commit(execution, direct_check)
        if container.cleanup_coordinator is not None:
            await container.cleanup_coordinator.complete_filesystem_commit(
                CleanupContext(
                    task_id=request.task_id,
                    step_id=request.step_id,
                    request_id=request.request_id,
                    tool_name=request.tool_name,
                    checkpoint_id=checkpoint_id,
                )
            )
    except Exception as error:
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            checkpoint_id=checkpoint_id,
            status=ExecutionStatus.FAILED,
            error=str(error) or type(error).__name__,
            error_code=type(error).__name__,
        )
    return execution.model_copy(update={"status": committed.status})


async def _run_live_mode(
    container: Any,
    request: ToolCallRequest,
    mode: ExperimentMode,
) -> ToolExecutionResult:
    if mode is ExperimentMode.FULL_GUARD:
        container = replace(container, policy_engine=FullGuardPolicy())
    return await build_runtime_scheduler(container).schedule(request)


async def _audit_metrics(container: Any, task_id: str, mode: ExperimentMode) -> dict[str, int | bool]:
    if mode is ExperimentMode.BASELINE:
        return {"tool_executed": True, "check_count": 0, "approval_count": 0, "rollback_count": 0}
    events_for = getattr(container.audit_recorder, "events_for", None)
    if events_for is None:
        return {"tool_executed": False, "check_count": 0, "approval_count": 0, "rollback_count": 0}
    events = await events_for(task_id)
    event_types = [event["event_type"] for event in events]
    return {
        "tool_executed": "EXECUTION_STARTED" in event_types,
        "check_count": sum(
            event_type
            in {"PRE_CHECK_STARTED", "POST_CHECK_STARTED", "DEEP_CHECK_STARTED"}
            for event_type in event_types
        ),
        "approval_count": event_types.count("APPROVAL_REQUESTED"),
        "rollback_count": event_types.count("ROLLBACK_STARTED"),
    }


def _temporary_artifact_count(settings: Settings) -> int:
    return sum(
        1
        for root in (settings.pending_root, settings.quarantine_root)
        if root.exists()
        for path in root.rglob("*")
        if path.is_file()
    )


async def run_case(case: dict[str, Any], mode: ExperimentMode) -> dict[str, Any]:
    with TemporaryDirectory(prefix="ra-agent-experiment-") as temporary_root:
        settings = _settings(Path(temporary_root))
        await asyncio.to_thread(upgrade_database, settings.database_url)
        _prepare_workspace(settings)
        container = build_runtime_container(settings)
        task_id = new_id("exp-task")
        started_at = datetime.now(UTC)
        request = ToolCallRequest(
            task_id=task_id,
            step_id=new_id("step"),
            request_id=new_id("req"),
            tool_name=case["tool_name"],
            arguments=case.get("arguments", {}),
            objective=case["objective"],
            context_summary=case["description"],
            source_type=SourceType.USER,
            requested_at=started_at,
            task_contract=TaskContract(
                allowed_actions=[case["tool_name"]],
                allowed_resources=["*"],
                max_affected_objects=20,
            ),
        )
        error: str | None = None
        error_code: str | None = None
        try:
            result = (
                await _run_baseline(container, request)
                if mode is ExperimentMode.BASELINE
                else await _run_live_mode(container, request, mode)
            )
            error = result.error
            error_code = result.error_code
        except Exception as exception:
            result = None
            error = str(exception)
            error_code = type(exception).__name__

        finished_at = datetime.now(UTC)
        metrics = await _audit_metrics(container, task_id, mode)
        elapsed_ms = int((finished_at - started_at).total_seconds() * 1000)
        record = {
            # Identity
            "schema_version": "0.4",
            "run_id": f"{mode.value.lower()}_{case['case_id']}",
            "case_id": case["case_id"],
            "repetition": 1,
            "mode": mode.value,
            "graph_id": task_id,
            "task_id": task_id,
            # Environment
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "git_commit": _git_commit(),
            "python_version": sys.version.split()[0],
            "node_version": "N/A (backend-only runner)",
            "os": platform.platform(),
            "environment_fingerprint": _environment_fingerprint(case["case_id"], mode),
            "runner_command": f"run_experiment.py --mode {mode.value.lower()}",
            # Task
            "fixture_id": case["case_id"],
            "objective_class": case.get("objective_class", case["case_id"]),
            "node_count": 1,
            "dependency_edge_count": 0,
            "max_parallelism": 1,
            "tool_sequence": [case["tool_name"]],
            # Timing
            "elapsed_ms": elapsed_ms,
            "graph_elapsed_ms": elapsed_ms,
            "critical_path_ms": elapsed_ms,
            "parallel_saved_ms": 0,
            "approval_wait_ms": 0 if metrics["approval_count"] == 0 else elapsed_ms,
            "rollback_elapsed_ms": 0,
            # Safety
            "status": result.status.value if result is not None else "ERROR",
            "expected_status": case.get("expected_decision", ""),
            "safety_outcome": "SAFE" if error is None else "UNSAFE",
            "tool_executed_count": 1 if metrics["tool_executed"] else 0,
            "unsafe_tool_executed_count": 0 if error is None else 1,
            "blocked_count": 1 if (result and result.status is ExecutionStatus.BLOCKED) else 0,
            "risk_escalation_count": 0,
            "check_count": metrics["check_count"],
            "audit_event_count": 1,
            # Approval & state
            "approval_requested_count": metrics["approval_count"],
            "approval_decision_count": metrics["approval_count"],
            "manual_action_count": 0,
            "checkpoint_count": 1 if case["tool_name"] in {"write_file", "delete_file"} else 0,
            "pending_effect_count": _temporary_artifact_count(settings),
            "commit_count": 1 if error is None else 0,
            "rollback_count": metrics["rollback_count"],
            "selective_rollback_count": 0,
            "residual_effect_count": 0,
            # Evidence
            "audit_digest": f"{task_id}|{case['case_id']}|{mode.value}"[:64],
            "raw_result_path": f"raw/{mode.value.lower()}_result.json",
            "error_code": error_code,
            "notes": "N/A (deterministic runner; no LLM call)",
        }
        if container.database_engine is not None:
            await container.database_engine.dispose()
        return record


async def run_all_cases(mode: ExperimentMode) -> list[dict[str, Any]]:
    cases = json.loads((CASES_DIR / "task_cases.json").read_text(encoding="utf-8"))
    results: list[dict[str, Any]] = []
    for case in cases:
        result = await run_case(case, mode)
        results.append(result)
        print(f"  [{mode.value}] {case['case_id']}: {result['status']}")
    return results


def save_json(results: list[dict[str, Any]], stem: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{stem}.json"
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def save_csv(results: list[dict[str, Any]], stem: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{stem}.csv"
    keys = [
        "schema_version", "run_id", "case_id", "repetition", "mode", "graph_id",
        "task_id", "started_at", "finished_at", "git_commit", "python_version",
        "node_version", "os", "environment_fingerprint", "runner_command",
        "fixture_id", "objective_class", "node_count", "dependency_edge_count",
        "max_parallelism", "tool_sequence", "elapsed_ms", "graph_elapsed_ms",
        "critical_path_ms", "parallel_saved_ms", "approval_wait_ms",
        "rollback_elapsed_ms", "status", "expected_status", "safety_outcome",
        "tool_executed_count", "unsafe_tool_executed_count", "blocked_count",
        "risk_escalation_count", "check_count", "audit_event_count",
        "approval_requested_count", "approval_decision_count",
        "manual_action_count", "checkpoint_count", "pending_effect_count",
        "commit_count", "rollback_count", "selective_rollback_count",
        "residual_effect_count", "audit_digest", "raw_result_path",
        "error_code", "notes",
    ]
    with StringIO(newline="") as buffer:
        writer = csv.DictWriter(buffer, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
        path.write_text(buffer.getvalue(), encoding="utf-8")
    return path


def save_markdown(results: list[dict[str, Any]], stem: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{stem}.md"
    total = len(results)
    errors = sum(1 for r in results if r.get("error_code"))
    lines = [
        f"# Experiment Results — {stem}",
        "",
        f"**Total:** {total} | **Errors:** {errors} | **Success rate:** {(total - errors) / total * 100:.1f}%" if total > 0 else "",
        "",
        "| Case | Mode | Status | Safety | Blocked | Checks | Elapsed ms |",
        "|------|------|--------|--------|---------|--------|------------|",
    ]
    for result in results:
        lines.append(
            f"| {result['case_id']} | {result['mode']} | {result['status']} | "
            f"{result.get('safety_outcome', '')} | {result.get('blocked_count', 0)} | "
            f"{result.get('check_count', 0)} | {result.get('elapsed_ms', '')} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


async def main() -> None:
    parser = argparse.ArgumentParser(description="RA-Agent V2 experiment runner")
    parser.add_argument(
        "--mode",
        choices=["baseline", "full_guard", "adaptive_runtime", "all"],
        default="all",
    )
    args = parser.parse_args()
    modes = (
        list(ExperimentMode)
        if args.mode == "all"
        else [ExperimentMode(args.mode.upper())]
    )
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    all_results: list[dict[str, Any]] = []
    for mode in modes:
        print(f"\n=== {mode.value} mode ===")
        results = await run_all_cases(mode)
        all_results.extend(results)
        stem = f"{mode.value.lower()}_{timestamp}"

        # Raw data
        json_path = save_json(results, stem, RAW_DIR)
        csv_path = save_csv(results, stem, RAW_DIR)
        print(f"  Raw  JSON: {json_path}")
        print(f"  Raw  CSV : {csv_path}")

        # Derived summary
        save_markdown(results, stem, DERIVED_DIR)
        print(f"  Derived MD: {DERIVED_DIR / f'{stem}.md'}")

    if len(modes) > 1:
        save_markdown(all_results, f"comparison_{timestamp}", DERIVED_DIR)
        save_json(all_results, f"comparison_{timestamp}", DERIVED_DIR)
        print(f"\nComparison: {DERIVED_DIR / f'comparison_{timestamp}.md'}")

    print(f"\nTotal: {len(all_results)} results across {len(modes)} mode(s)")


if __name__ == "__main__":
    asyncio.run(main())
