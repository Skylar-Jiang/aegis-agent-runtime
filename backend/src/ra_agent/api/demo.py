"""Opt-in, local-only orchestration for reproducible Runtime demonstrations."""

from __future__ import annotations

import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ra_agent.contracts import (
    APIResponse,
    AuditEventType,
    RollbackPlan,
    SourceType,
    TaskContract,
    TaskGraph,
    TaskNode,
    ToolCallRequest,
)
from ra_agent.core.container import ServiceContainer
from ra_agent.core.ids import new_id
from ra_agent.execution.recovery import DependencyRecoveryPlanner

from .deps import get_services
from .task_graphs import TaskGraphRun, _run_graph, _runs, _scheduler

router = APIRouter(prefix="/api/demo", tags=["demo"])

_ROOT = "demo_workspace"
_FILES = {
    "docs/project_brief.md": (
        "# Project brief\n\nPrepare a concise project summary for the local demonstration.\n"
    ),
    "docs/security_notes.md": (
        "# Security notes\n\nReview changes through the Runtime approval boundary.\n"
    ),
    "protected/legacy_config.json": '{"version": 1, "feature": "legacy"}\n',
    "recovery/source.txt": "runtime-source\n",
    "recovery/derived.txt": "runtime-derived\n",
    "recovery/independent.txt": "runtime-independent\n",
}


class RecoverySelection(BaseModel):
    selected_effect_ids: list[str] = Field(min_length=1)


def _enabled(request: Request) -> None:
    if not getattr(request.app.state, "enable_demo_fixtures", False):
        raise HTTPException(status_code=404, detail="Not found")


def _workspace(request: Request) -> Path:
    settings = getattr(request.app.state, "runtime_settings", None)
    if settings is None:
        raise HTTPException(status_code=409, detail="Demo workspace settings are unavailable")
    root = settings.workspace_root.resolve()
    workspace = (root / _ROOT).resolve()
    if root not in workspace.parents:
        raise HTTPException(status_code=409, detail="Demo workspace is outside Runtime workspace")
    return workspace


def _reset_workspace(request: Request) -> Path:
    workspace = _workspace(request)
    if workspace.exists():
        shutil.rmtree(workspace)
    for relative, content in _FILES.items():
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (workspace / "output").mkdir(exist_ok=True)
    return workspace


def _request(
    task_id: str, node_id: str, tool_name: str, arguments: dict[str, str]
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id=task_id,
        step_id=node_id,
        request_id=new_id("demo-request"),
        tool_name=tool_name,
        arguments=arguments,
        objective="Run a local, reproducible Aegis Runtime demonstration",
        context_summary="Trusted local demo fixture; Runtime policy remains authoritative.",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
        task_contract=TaskContract(
            allowed_actions=["read_file", "write_file", "delete_file"],
            allowed_resources=["demo_workspace/*"],
            max_affected_objects=8,
            allow_egress=False,
        ),
    )


def _node(
    task_id: str,
    graph_id: str,
    node_id: str,
    tool_name: str,
    arguments: dict[str, str],
    dependencies: list[str] | None = None,
) -> TaskNode:
    return TaskNode(
        task_id=task_id,
        graph_id=graph_id,
        node_id=node_id,
        request=_request(task_id, node_id, tool_name, arguments),
        dependencies=dependencies or [],
        parallel_safe=True,
    )


def _scenario_graph(name: str) -> TaskGraph:
    task_id = new_id(f"demo-{name}-task")
    graph_id = new_id(f"demo-{name}-graph")
    protected = f"{_ROOT}/protected/legacy_config.json"
    if name == "boundary":
        nodes = [_node(task_id, graph_id, "D1", "delete_file", {"path": protected})]
    elif name == "scheduling":
        nodes = [
            _node(task_id, graph_id, "N1", "read_file", {"path": f"{_ROOT}/docs/project_brief.md"}),
            _node(
                task_id,
                graph_id,
                "N2",
                "write_file",
                {"path": f"{_ROOT}/output/summary.md", "content": "Runtime summary\n"},
                ["N1"],
            ),
            _node(task_id, graph_id, "N3", "delete_file", {"path": protected}),
            _node(
                task_id, graph_id, "N4", "read_file", {"path": f"{_ROOT}/docs/security_notes.md"}
            ),
            _node(
                task_id,
                graph_id,
                "N5",
                "write_file",
                {
                    "path": f"{_ROOT}/output/security_notes.md",
                    "content": "Security notes reviewed\n",
                },
                ["N4"],
            ),
            _node(
                task_id,
                graph_id,
                "N6",
                "write_file",
                {"path": f"{_ROOT}/output/cleanup_receipt.md", "content": "Cleanup approved\n"},
                ["N3"],
            ),
        ]
    elif name == "recovery":
        nodes = [
            _node(
                task_id,
                graph_id,
                "R1",
                "write_file",
                {"path": f"{_ROOT}/recovery/source.txt", "content": "runtime-source-updated\n"},
            ),
            _node(
                task_id,
                graph_id,
                "R2",
                "write_file",
                {"path": f"{_ROOT}/recovery/derived.txt", "content": "runtime-derived-updated\n"},
                ["R1"],
            ),
            _node(
                task_id,
                graph_id,
                "R3",
                "write_file",
                {
                    "path": f"{_ROOT}/recovery/independent.txt",
                    "content": "runtime-independent-updated\n",
                },
            ),
        ]
    elif name == "conflict":
        nodes = [
            _node(
                task_id,
                graph_id,
                "C1",
                "write_file",
                {"path": f"{_ROOT}/output/conflict.txt", "content": "serialized write\n"},
            ),
            _node(task_id, graph_id, "C2", "read_file", {"path": f"{_ROOT}/output/conflict.txt"}),
            _node(task_id, graph_id, "C3", "read_file", {"path": f"{_ROOT}/docs/project_brief.md"}),
        ]
    else:
        raise HTTPException(status_code=404, detail="Unknown demo scenario")
    return TaskGraph(graph_id=graph_id, task_id=task_id, nodes=nodes, max_parallelism=2)


async def _record_recovery(
    services: ServiceContainer,
    task_id: str,
    event_type: AuditEventType,
    status: str,
    summary: str,
    details: dict[str, object],
) -> None:
    await services.audit_recorder.record(
        task_id=task_id,
        event_type=event_type,
        actor="demo-runtime-orchestrator",
        status=status,
        summary=summary,
        details=details,
    )


async def _selected_recovery_plan(
    task_id: str,
    request: Request,
    services: ServiceContainer,
    selection: RecoverySelection | None,
):
    run = next((item for item in _runs(request).values() if item.graph.task_id == task_id), None)
    if run is None or run.demo_recovery_root_node_id is None:
        raise HTTPException(status_code=404, detail="Recovery demo task not found")
    if services.effect_store is None or services.selective_rollback_executor is None:
        raise HTTPException(status_code=409, detail="Live effect recovery is unavailable")
    if run.background is not None and not run.background.done():
        await run.background
    effects = await services.effect_store.list_by_task_id(task_id)
    if selection is None:
        root_request_id = next(
            node.request.request_id
            for node in run.graph.nodes
            if node.node_id == run.demo_recovery_root_node_id
        )
        selected_effect_ids = [
            effect.effect_id for effect in effects if effect.request_id == root_request_id
        ]
    else:
        selected_effect_ids = selection.selected_effect_ids
    known_effect_ids = {effect.effect_id for effect in effects}
    if len(selected_effect_ids) != len(set(selected_effect_ids)) or not set(
        selected_effect_ids
    ).issubset(known_effect_ids):
        raise HTTPException(
            status_code=422,
            detail="Selected effects must belong to this recovery task",
        )
    recovery = await DependencyRecoveryPlanner(services.effect_store).plan(
        task_id=task_id,
        failed_effect_ids=selected_effect_ids,
        trigger="demo_cancelled_after_runtime_commit",
    )
    return run, effects, recovery, sorted(selected_effect_ids)


def _recovery_view(
    recovery,
    selected_effect_ids: list[str],
    conflict_effect_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "trigger": "Cancel",
        "selected_effect_ids": selected_effect_ids,
        "affected_effect_ids": recovery.affected_effect_ids,
        "rollback_effect_ids": recovery.rollback_effect_ids,
        "preserve_effect_ids": recovery.preserve_effect_ids,
        "conflict_effect_ids": conflict_effect_ids or [],
    }


@router.post("/reset")
async def reset_demo_workspace(request: Request) -> APIResponse[dict[str, object]]:
    _enabled(request)
    workspace = _reset_workspace(request)
    return APIResponse(
        data={"workspace": _ROOT, "files": sorted(_FILES), "output": str(workspace / "output")}
    )


@router.post("/scenarios/{name}")
async def start_demo_scenario(request: Request, name: str) -> APIResponse[dict[str, str]]:
    _enabled(request)
    _reset_workspace(request)
    graph = _scenario_graph(name)
    scheduler = _scheduler(request)
    await scheduler.prepare_graph(graph)
    run = TaskGraphRun(graph=graph, created_at=datetime.now(UTC))
    if name == "recovery":
        run.demo_recovery_root_node_id = "R1"
    _runs(request)[graph.graph_id] = run
    run.background = asyncio.create_task(_run_graph(run, scheduler))
    return APIResponse(
        data={"task_id": graph.task_id, "graph_id": graph.graph_id, "scenario": name}
    )


@router.post("/scenarios/{task_id}/user-modification")
async def apply_demo_user_modification(
    task_id: str, request: Request, services: Annotated[ServiceContainer, Depends(get_services)]
) -> APIResponse[dict[str, str]]:
    _enabled(request)
    run = next((item for item in _runs(request).values() if item.graph.task_id == task_id), None)
    if run is None or run.demo_recovery_root_node_id is None:
        raise HTTPException(status_code=404, detail="Recovery demo task not found")
    target = _workspace(request) / "recovery" / "derived.txt"
    target.write_text("user-change\n", encoding="utf-8")
    await _record_recovery(
        services,
        task_id,
        AuditEventType.EXECUTION_FINISHED,
        "USER_MODIFIED",
        "user changed derived recovery target after Runtime commit",
        {"target_ref": f"file:{_ROOT}/recovery/derived.txt"},
    )
    return APIResponse(
        data={
            "task_id": task_id,
            "target": f"{_ROOT}/recovery/derived.txt",
            "status": "USER_MODIFIED",
        }
    )


@router.get("/scenarios/{task_id}/recovery")
async def get_demo_recovery(
    task_id: str, request: Request
) -> APIResponse[dict[str, object] | None]:
    _enabled(request)
    run = next((item for item in _runs(request).values() if item.graph.task_id == task_id), None)
    if run is None or run.demo_recovery_root_node_id is None:
        raise HTTPException(status_code=404, detail="Recovery demo task not found")
    return APIResponse(data=run.recovery_view)


@router.post("/scenarios/{task_id}/recovery-preview")
async def preview_demo_recovery(
    task_id: str,
    selection: RecoverySelection,
    request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[dict[str, object]]:
    _enabled(request)
    _, _, recovery, selected_effect_ids = await _selected_recovery_plan(
        task_id, request, services, selection
    )
    return APIResponse(data=_recovery_view(recovery, selected_effect_ids))


@router.post("/scenarios/{task_id}/recover")
async def recover_demo_scenario(
    task_id: str,
    request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
    selection: RecoverySelection | None = None,
) -> APIResponse[dict[str, object]]:
    _enabled(request)
    run, effects, recovery, selected_effect_ids = await _selected_recovery_plan(
        task_id, request, services, selection
    )
    selected = [
        effect for effect in effects if effect.effect_id in set(recovery.rollback_effect_ids)
    ]
    plan = RollbackPlan(
        plan_id=recovery.rollback_plan_id,
        task_id=task_id,
        trigger=recovery.trigger,
        effect_ids=recovery.rollback_effect_ids,
        request_ids=sorted(effect.request_id for effect in selected),
        checkpoint_ids=sorted(
            effect.checkpoint_id for effect in selected if effect.checkpoint_id is not None
        ),
        reason=recovery.reason,
    )
    await _record_recovery(
        services,
        task_id,
        AuditEventType.TASK_CANCELLED,
        "CANCELLED",
        "demo cancellation triggered dependency-aware recovery",
        {
            "selected_effect_ids": selected_effect_ids,
            "affected_effect_ids": recovery.affected_effect_ids,
        },
    )
    await _record_recovery(
        services,
        task_id,
        AuditEventType.ROLLBACK_STARTED,
        "ROLLING_BACK",
        "dependency-aware recovery started",
        {"rollback_plan_id": plan.plan_id, "affected_effect_ids": recovery.affected_effect_ids},
    )
    rollback_executor = services.selective_rollback_executor
    if rollback_executor is None:
        raise HTTPException(status_code=409, detail="Live effect recovery is unavailable")
    result = await rollback_executor.execute_rollback_plan(plan)
    statuses = {
        effect.effect_id: "ROLLED_BACK"
        for effect in effects
        if effect.request_id in set(result.rolled_back_request_ids)
    }
    statuses.update(
        {
            effect.effect_id: "CONFLICT"
            for effect in effects
            if effect.request_id in set(result.failed_request_ids)
        }
    )
    statuses.update({effect_id: "PRESERVED" for effect_id in recovery.preserve_effect_ids})
    run.recovery_statuses = statuses
    run.recovery_view = _recovery_view(
        recovery,
        selected_effect_ids,
        [
            effect.effect_id
            for effect in effects
            if effect.request_id in set(result.failed_request_ids)
        ],
    )
    await _record_recovery(
        services,
        task_id,
        AuditEventType.ROLLBACK_FINISHED,
        "CONFLICT" if result.failed_request_ids else "ROLLED_BACK",
        "dependency-aware recovery finished",
        {**run.recovery_view, "rollback_plan_id": plan.plan_id},
    )
    for effect_id in recovery.preserve_effect_ids:
        await _record_recovery(
            services,
            task_id,
            AuditEventType.EXECUTION_FINISHED,
            "PRESERVED",
            "independent effect preserved outside the recovery closure",
            {"effect_id": effect_id},
        )
    return APIResponse(data=run.recovery_view)
