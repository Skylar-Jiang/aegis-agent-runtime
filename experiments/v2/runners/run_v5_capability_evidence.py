"""Run the frozen V5 runtime-capability fixtures against local Aegis code."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import platform
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from ra_agent.audit import InMemoryAuditRecorder  # noqa: E402
from ra_agent.contracts import (  # noqa: E402
    ExecutionStatus,
    SourceType,
    TaskContract,
    TaskGraph,
    TaskNode,
    ToolCallRequest,
)
from ra_agent.core.bootstrap import (  # noqa: E402
    build_runtime_container,
    build_runtime_scheduler,
    build_task_graph_scheduler,
)
from ra_agent.core.config import RuntimeMode, Settings  # noqa: E402
from ra_agent.database.migrate import upgrade_database  # noqa: E402
from ra_agent.execution.executor import RegistryToolExecutor  # noqa: E402
from ra_agent.execution.recovery import DependencyRecoveryPlanner  # noqa: E402
from ra_agent.runtime.effect_targets import infer_effect_targets  # noqa: E402
from ra_agent.tools import ToolRegistry  # noqa: E402

FIXTURE_VERSION = "v5-capability-freeze-20260822"


@dataclass(frozen=True)
class Case:
    case_id: str
    tool_name: str
    arguments: dict[str, object]
    objective: str


DECISION_CASES = (
    Case(
        "temporary_write",
        "write_file",
        {"path": "scratch.txt", "content": "ok"},
        "Write a temporary note.",
    ),
    Case(
        "protected_config_write",
        "write_file",
        {"path": "configs/risk_rules.yaml", "content": "v: 1"},
        "Change project security configuration.",
    ),
    Case("sensitive_read", "read_file", {"path": ".env"}, "Read stored credentials."),
)


class _TrackingHandler:
    def __init__(
        self, name: str, handler: object, trace: list[dict[str, Any]], pause: float
    ) -> None:
        self._name, self._handler, self._trace, self._pause = (
            name,
            handler,
            trace,
            pause,
        )

    async def __call__(self, request: ToolCallRequest):
        self._trace.append(
            {
                "request_id": request.request_id,
                "phase": "start",
                "at": datetime.now(UTC).isoformat(),
                "tool": self._name,
            }
        )
        try:
            if self._pause:
                await asyncio.sleep(self._pause)
            return await self._handler(request)
        finally:
            self._trace.append(
                {
                    "request_id": request.request_id,
                    "phase": "end",
                    "at": datetime.now(UTC).isoformat(),
                    "tool": self._name,
                }
            )


def _settings(root: Path) -> Settings:
    runtime = root / ".runtime"
    return Settings.model_validate(
        {
            "runtime_mode": RuntimeMode.LIVE_AGENT,
            "database_url": f"sqlite+aiosqlite:///{(root / 'runtime.db').as_posix()}",
            "security_config_dir": ROOT / "configs",
            "workspace_root": runtime / "workspace",
            "pending_root": runtime / "pending",
            "checkpoint_root": runtime / "checkpoints",
            "quarantine_root": runtime / "quarantine",
        }
    )


async def _runtime(root: Path, *, pause: bool = False):
    settings = _settings(root)
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    (settings.workspace_root / "input.txt").write_text("safe input", encoding="utf-8")
    await asyncio.to_thread(upgrade_database, settings.database_url)
    container = build_runtime_container(settings)
    trace: list[dict[str, Any]] = []
    registry = ToolRegistry()
    for spec in container.tool_registry.list_specs():
        registry.register(
            spec,
            _TrackingHandler(
                spec.name,
                container.tool_registry.get_handler(spec.name),
                trace,
                0.025 if pause else 0.0,
            ),
        )
    executor = RegistryToolExecutor(
        registry,
        container.tool_executor._pending_store,
        memory_store=container.tool_executor._memory_store,
        quarantine_store=container.tool_executor._quarantine_store,
        cleanup_coordinator=container.cleanup_coordinator,
        effect_manager=container.effect_manager,
    )
    recorder = InMemoryAuditRecorder()
    return (
        settings,
        replace(
            container,
            tool_registry=registry,
            tool_executor=executor,
            audit_recorder=recorder,
        ),
        trace,
        recorder,
    )


def _contract(*tools: str) -> TaskContract:
    return TaskContract(
        allowed_actions=list(tools),
        allowed_resources=["*"],
        max_affected_objects=8,
        allow_egress=True,
    )


def _request(case: Case, task_id: str) -> ToolCallRequest:
    return ToolCallRequest(
        task_id=task_id,
        step_id=f"step-{case.case_id}",
        request_id=f"request-{case.case_id}",
        tool_name=case.tool_name,
        arguments=case.arguments,
        objective=case.objective,
        context_summary="V5 frozen capability fixture",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
        task_contract=_contract(case.tool_name),
    )


def _node(
    task_id: str,
    graph_id: str,
    node_id: str,
    tool_name: str,
    arguments: dict[str, object],
    *,
    dependencies: list[str] | None = None,
    hints: list[str] | None = None,
) -> TaskNode:
    return TaskNode(
        task_id=task_id,
        graph_id=graph_id,
        node_id=node_id,
        request=ToolCallRequest(
            task_id=task_id,
            step_id=node_id,
            request_id=f"{graph_id}-{node_id}",
            tool_name=tool_name,
            arguments=arguments,
            objective="V5 runtime target analysis",
            context_summary="V5 graph fixture",
            source_type=SourceType.USER,
            requested_at=datetime.now(UTC),
            task_contract=_contract("write_file", "read_file", "list_dir"),
        ),
        dependencies=dependencies or [],
        parallel_safe=True,
        effect_targets=hints or [],
    )


def _git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def _row(**values: Any) -> dict[str, Any]:
    values.setdefault("schema_version", "v5-capability-1")
    values.setdefault("timestamp", datetime.now(UTC).isoformat())
    values.setdefault("fixture_version", FIXTURE_VERSION)
    values.setdefault("git_commit", _git_commit())
    values.setdefault(
        "git_dirty",
        bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"],
                cwd=ROOT,
                encoding="utf-8",
                errors="replace",
            ).strip()
        ),
    )
    values.setdefault(
        "environment", {"python": platform.python_version(), "os": platform.platform()}
    )
    values.setdefault("repeat_count", 1)
    values.setdefault("error_code", None)
    return values


def _intervals(trace: list[dict[str, Any]]) -> dict[str, tuple[datetime, datetime]]:
    positions: dict[str, dict[str, datetime]] = defaultdict(dict)
    for item in trace:
        positions[item["request_id"]][item["phase"]] = datetime.fromisoformat(
            item["at"]
        )
    return {
        key: (value["start"], value["end"])
        for key, value in positions.items()
        if {"start", "end"} <= set(value)
    }


async def _decision_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in DECISION_CASES:
        root = Path(tempfile.mkdtemp(prefix="aegis-v5-decision-"))
        try:
            settings, container, trace, recorder = await _runtime(root)
            request = _request(case, f"v5-decision-{case.case_id}")
            verdict = await container.risk_classifier.classify(request)
            result = await build_runtime_scheduler(container).schedule(request)
            spec = container.tool_registry.get_spec(case.tool_name)
            rows.append(
                _row(
                    evidence_set="runtime_decision",
                    case_id=case.case_id,
                    tool_name=case.tool_name,
                    tool_spec=spec.model_dump(mode="json"),
                    effect_type=spec.side_effect_type,
                    inferred_effect_targets=[
                        target.render() for target in infer_effect_targets(request)
                    ],
                    risk_level=verdict.risk_level.value,
                    runtime_decision=verdict.recommended_decision.value,
                    requires_approval=result.status is ExecutionStatus.WAITING_APPROVAL,
                    checkpoint_required=verdict.requires_checkpoint,
                    checkpoint_entered=any(
                        event.event_type.value == "CHECKPOINT_CREATED"
                        for event in recorder.events_for(request.task_id)
                    ),
                    final_status=result.status.value,
                    handler_calls=sum(item["phase"] == "start" for item in trace),
                    audit_event_types=[
                        event.event_type.value
                        for event in recorder.events_for(request.task_id)
                    ],
                )
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)
    return rows


async def _conflict_rows() -> list[dict[str, Any]]:
    fixtures = (
        (
            "independent_files",
            "write_file",
            {"path": "left.txt", "content": "L"},
            "write_file",
            {"path": "right.txt", "content": "R"},
            False,
        ),
        (
            "read_write_exact",
            "read_file",
            {"path": "shared.txt"},
            "write_file",
            {"path": "shared.txt", "content": "new"},
            True,
        ),
        (
            "directory_subtree",
            "list_dir",
            {"path": "project"},
            "write_file",
            {"path": "project/subdir/file.txt", "content": "new"},
            True,
        ),
        (
            "normalized_alias",
            "write_file",
            {"path": "project/./config.yaml", "content": "a"},
            "write_file",
            {"path": "project/config.yaml", "content": "b"},
            True,
        ),
    )
    rows: list[dict[str, Any]] = []
    for (
        case_id,
        first_tool,
        first_args,
        second_tool,
        second_args,
        expected_conflict,
    ) in fixtures:
        root = Path(tempfile.mkdtemp(prefix="aegis-v5-conflict-"))
        try:
            settings, container, trace, _ = await _runtime(root, pause=True)
            (settings.workspace_root / "shared.txt").write_text("old", encoding="utf-8")
            (settings.workspace_root / "project" / "subdir").mkdir(
                parents=True, exist_ok=True
            )
            task_id, graph_id = f"v5-{case_id}", f"graph-{case_id}"
            nodes = [
                _node(
                    task_id,
                    graph_id,
                    "left",
                    first_tool,
                    first_args,
                    hints=["file:misleading-left"],
                ),
                _node(
                    task_id,
                    graph_id,
                    "right",
                    second_tool,
                    second_args,
                    hints=["file:misleading-right"],
                ),
            ]
            graph = TaskGraph(
                graph_id=graph_id, task_id=task_id, nodes=nodes, max_parallelism=2
            )
            scheduler = build_task_graph_scheduler(
                container, runtime_scheduler=build_runtime_scheduler(container)
            )
            result = await scheduler.schedule_graph(graph)
            intervals = _intervals(trace)
            first_interval, second_interval = (
                intervals.get(node.request.request_id) for node in nodes
            )
            overlap = bool(
                first_interval
                and second_interval
                and max(first_interval[0], second_interval[0])
                < min(first_interval[1], second_interval[1])
            )
            rows.append(
                _row(
                    evidence_set="effect_target_conflict",
                    case_id=case_id,
                    semantic_conflict_expected=expected_conflict,
                    planner_hints={node.node_id: node.effect_targets for node in nodes},
                    inferred_effect_targets=result.inferred_effect_targets,
                    inferred_conflicts=result.inferred_conflicts,
                    actual_overlap=overlap,
                    peak_concurrency=2 if overlap else 1,
                    max_parallelism=2,
                    final_statuses={
                        key: value.status.value
                        for key, value in result.node_results.items()
                    },
                    pass_condition=(not expected_conflict and overlap)
                    or (expected_conflict and not overlap),
                )
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)
    return rows


async def _planner_rows() -> list[dict[str, Any]]:
    root = Path(tempfile.mkdtemp(prefix="aegis-v5-planner-"))
    try:
        settings, container, trace, _ = await _runtime(root, pause=True)
        (settings.workspace_root / "same.txt").write_text("old", encoding="utf-8")
        task_id, graph_id = "v5-planner-task", "v5-planner-graph"
        nodes = [
            _node(
                task_id,
                graph_id,
                "proposal-read",
                "read_file",
                {"path": "same.txt"},
                hints=["file:unrelated-a"],
            ),
            _node(
                task_id,
                graph_id,
                "proposal-write",
                "write_file",
                {"path": "same.txt", "content": "x"},
                hints=["file:unrelated-b"],
            ),
        ]
        scheduler = build_task_graph_scheduler(
            container, runtime_scheduler=build_runtime_scheduler(container)
        )
        result = await scheduler.schedule_graph(
            TaskGraph(
                graph_id=graph_id, task_id=task_id, nodes=nodes, max_parallelism=2
            )
        )
        return [
            _row(
                evidence_set="planner_perturbation",
                evidence_level="DETERMINISTIC_UNTRUSTED_GRAPH_PROPOSAL",
                case_id="misleading_conflict_hints",
                planner_proposal={
                    "dependencies": [],
                    "effect_targets": {
                        node.node_id: node.effect_targets for node in nodes
                    },
                },
                runtime_analysis={
                    "targets": result.inferred_effect_targets,
                    "conflicts": result.inferred_conflicts,
                },
                final_execution={
                    key: value.status.value
                    for key, value in result.node_results.items()
                },
                actual_overlap=(
                    lambda intervals: bool(
                        len(intervals) == 2
                        and max(start for start, _ in intervals.values())
                        < min(end for _, end in intervals.values())
                    )
                )(_intervals(trace)),
                handler_calls=sum(item["phase"] == "start" for item in trace),
                pass_condition=bool(result.inferred_conflicts),
            ),
            _row(
                evidence_set="planner_perturbation",
                evidence_level="INTERFACE_AUDIT",
                case_id="agent_taskgraph_proposal",
                final_execution="SKIPPED",
                skip_reason="AgentRuntime does not accept or emit TaskGraph proposals; no mock/replay is labelled real-agent.",
            ),
        ]
    finally:
        shutil.rmtree(root, ignore_errors=True)


async def _rollback_rows() -> list[dict[str, Any]]:
    async def fixture(*, user_modify: bool) -> tuple[dict[str, Any], dict[str, Any]]:
        root = Path(tempfile.mkdtemp(prefix="aegis-v5-rollback-"))
        try:
            settings, container, _, _ = await _runtime(root)
            task_id, graph_id = (
                ("v5-user-mod", "v5-user-mod-graph")
                if user_modify
                else ("v5-closure", "v5-closure-graph")
            )
            nodes = [
                _node(
                    task_id,
                    graph_id,
                    "upstream",
                    "write_file",
                    {"path": "upstream.txt", "content": "upstream"},
                ),
                _node(
                    task_id,
                    graph_id,
                    "derived",
                    "write_file",
                    {"path": "derived.txt", "content": "derived"},
                    dependencies=["upstream"],
                ),
                _node(
                    task_id,
                    graph_id,
                    "unrelated",
                    "write_file",
                    {"path": "unrelated.txt", "content": "unrelated"},
                ),
            ]
            scheduler = build_task_graph_scheduler(
                container, runtime_scheduler=build_runtime_scheduler(container)
            )
            await scheduler.schedule_graph(
                TaskGraph(
                    graph_id=graph_id, task_id=task_id, nodes=nodes, max_parallelism=2
                )
            )
            effects = await container.effect_store.list_by_task_id(task_id)
            upstream = next(
                effect
                for effect in effects
                if effect.request_id == nodes[0].request.request_id
            )
            plan = await DependencyRecoveryPlanner(container.effect_store).plan(
                task_id=task_id,
                failed_effect_ids=[upstream.effect_id],
                trigger="v5_injected_failure",
            )
            if user_modify:
                (settings.workspace_root / "derived.txt").write_text(
                    "user-change", encoding="utf-8"
                )
            result = await container.selective_rollback_executor.execute(
                plan.to_rollback_plan()
            )
            current = {
                path.name: path.read_text(encoding="utf-8")
                for path in settings.workspace_root.glob("*.txt")
            }
            row = _row(
                evidence_set="selective_rollback",
                case_id="user_modification_conflict"
                if user_modify
                else "dependency_closure",
                lineage_edges=[
                    {"from": effect.parent_effect_ids[0], "to": effect.effect_id}
                    for effect in effects
                    if effect.parent_effect_ids
                ],
                failed_effect_ids=plan.failed_effect_ids,
                affected_closure=plan.affected_effect_ids,
                rollback_scope=plan.rollback_effect_ids,
                preserved_scope=plan.preserve_effect_ids,
                rollback_status=result.status.value,
                rolled_back_request_ids=result.rolled_back_request_ids,
                failed_request_ids=result.failed_request_ids,
                workspace_after=current,
                preservation_correct=(current.get("unrelated.txt") == "unrelated"),
                user_modification_protected=(
                    not user_modify or current.get("derived.txt") == "user-change"
                ),
            )
            retry = (
                await container.selective_rollback_executor.execute(
                    plan.to_rollback_plan()
                )
                if not user_modify
                else None
            )
            retry_row = _row(
                evidence_set="selective_rollback",
                case_id="idempotent_retry"
                if not user_modify
                else "user_modification_retry_not_run",
                final_status=retry.status.value if retry else "NOT_RUN",
                rolled_back_request_ids=retry.rolled_back_request_ids if retry else [],
                failed_request_ids=retry.failed_request_ids if retry else [],
                pass_condition=bool(retry and retry.status is ExecutionStatus.SUCCESS)
                if not user_modify
                else True,
            )
            return row, retry_row
        finally:
            shutil.rmtree(root, ignore_errors=True)

    normal, retry = await fixture(user_modify=False)
    conflict, skipped = await fixture(user_modify=True)
    return [normal, retry, conflict, skipped]


def _write(rows: list[dict[str, Any]], output: Path) -> None:
    raw, derived = output / "raw", output / "derived"
    raw.mkdir(parents=True, exist_ok=True)
    derived.mkdir(parents=True, exist_ok=True)
    stem = "v5-capability-full"
    (raw / f"{stem}.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows
        ),
        encoding="utf-8",
    )
    fields = sorted({field for row in rows for field in row})
    with (raw / f"{stem}.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            {
                field: json.dumps(row.get(field), ensure_ascii=False, default=str)
                if isinstance(row.get(field), (dict, list))
                else row.get(field)
                for field in fields
            }
            for row in rows
        )
    summary = {
        "raw_row_count": len(rows),
        "by_evidence_set": {
            name: sum(row["evidence_set"] == name for row in rows)
            for name in sorted({row["evidence_set"] for row in rows})
        },
        "failed_or_skipped": sum(
            row.get("final_execution") == "SKIPPED"
            or row.get("final_status") == "SKIPPED"
            for row in rows
        ),
        "pass_conditions": sum(row.get("pass_condition") is True for row in rows),
    }
    (derived / "v5-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", required=True, type=Path)
    args = parser.parse_args()
    rows = asyncio.run(_decision_rows())
    rows.extend(asyncio.run(_conflict_rows()))
    rows.extend(asyncio.run(_planner_rows()))
    rows.extend(asyncio.run(_rollback_rows()))
    _write(rows, args.output_directory)
    print(f"wrote {len(rows)} V5 evidence rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
