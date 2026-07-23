"""Run deterministic, isolated Phase 3.5 experiments through real runtime components."""

import argparse
import asyncio
import csv
import json
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
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.core.bootstrap import build_runtime_container, build_runtime_scheduler
from ra_agent.core.config import RuntimeMode, Settings
from ra_agent.core.ids import new_id
from ra_agent.database.migrate import upgrade_database
from ra_agent.execution.cleanup import CleanupContext

RESULTS_DIR = Path(__file__).parent.parent / "results"
CASES_DIR = Path(__file__).parent.parent / "cases"
CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"


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
        )
        error: str | None = None
        error_code: str | None = None
        output: str | None = None
        try:
            result = (
                await _run_baseline(container, request)
                if mode is ExperimentMode.BASELINE
                else await _run_live_mode(container, request, mode)
            )
            error = result.error
            error_code = result.error_code
            output = str(result.output)[:200] if result.output else None
        except Exception as exception:
            result = None
            error = str(exception)
            error_code = type(exception).__name__

        finished_at = datetime.now(UTC)
        metrics = await _audit_metrics(container, task_id, mode)
        record = {
            "case_id": case["case_id"],
            "mode": mode.value,
            "task_id": task_id,
            "request_id": request.request_id,
            "status": result.status.value if result is not None else "ERROR",
            "expected_decision": case.get("expected_decision", ""),
            "tool_executed": metrics["tool_executed"],
            "check_count": metrics["check_count"],
            "approval_count": metrics["approval_count"],
            "rollback_count": metrics["rollback_count"],
            "temporary_artifact_count": _temporary_artifact_count(settings),
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "elapsed_ms": int((finished_at - started_at).total_seconds() * 1000),
            "result_output": output,
            "error": error,
            "error_code": error_code,
            "token_usage": "N/A (deterministic runner; no LLM call)",
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
        "case_id", "mode", "status", "expected_decision", "tool_executed", "check_count",
        "approval_count", "rollback_count", "temporary_artifact_count", "elapsed_ms", "error",
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
    lines = [
        f"# Experiment Results — {stem}",
        "",
        "| Case | Mode | Status | Tool ran | Checks | Approvals | Rollbacks | Temp artifacts | ms |",
        "|------|------|--------|----------|--------|-----------|-----------|----------------|----|",
    ]
    for result in results:
        lines.append(
            f"| {result['case_id']} | {result['mode']} | {result['status']} | "
            f"{result['tool_executed']} | {result['check_count']} | {result['approval_count']} | "
            f"{result['rollback_count']} | {result['temporary_artifact_count']} | "
            f"{result['elapsed_ms']} |"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


async def main() -> None:
    parser = argparse.ArgumentParser(description="RA-Agent real experiment runner")
    parser.add_argument("--mode", choices=["baseline", "full_guard", "adaptive_runtime", "all"], default="all")
    parser.add_argument("--output-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args()
    modes = (
        list(ExperimentMode)
        if args.mode == "all"
        else [ExperimentMode(args.mode.upper())]
    )
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    all_results: list[dict[str, Any]] = []
    for mode in modes:
        results = await run_all_cases(mode)
        all_results.extend(results)
        stem = f"{mode.value.lower()}_{timestamp}"
        save_json(results, stem, args.output_dir)
        save_csv(results, stem, args.output_dir)
        save_markdown(results, stem, args.output_dir)
    if len(modes) > 1:
        save_markdown(all_results, f"comparison_{timestamp}", args.output_dir)


if __name__ == "__main__":
    asyncio.run(main())
