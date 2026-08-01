"""In-process V2 TaskGraph API over the RuntimeTaskGraphScheduler."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Protocol, cast

from fastapi import APIRouter, Depends, HTTPException, Request

from ra_agent.contracts import (
    APIResponse,
    ApprovalRequest,
    ApprovalStatus,
    TaskGraph,
    TaskGraphResult,
)
from ra_agent.core.container import ServiceContainer

from .deps import get_services

graph_router = APIRouter(prefix="/api/task-graphs", tags=["task-graphs"])
task_graph_router = APIRouter(prefix="/api/tasks", tags=["task-graphs"])


class _GraphScheduler(Protocol):
    async def prepare_graph(self, graph: TaskGraph) -> TaskGraphResult: ...

    async def schedule_graph(self, graph: TaskGraph) -> TaskGraphResult: ...

    async def snapshot(self, graph_id: str) -> TaskGraphResult: ...

    async def cancel_graph(self, graph_id: str) -> TaskGraphResult: ...

    async def resume_after_approval(self, graph_id: str, approval_id: str) -> TaskGraphResult: ...


@dataclass(slots=True)
class TaskGraphRun:
    graph: TaskGraph
    created_at: datetime
    background: asyncio.Task[None] | None = None
    error_code: str | None = None


def _runs(request: Request) -> dict[str, TaskGraphRun]:
    runs = getattr(request.app.state, "task_graph_runs", None)
    if runs is None:
        runs = {}
        request.app.state.task_graph_runs = runs
    return runs


def is_known_graph_task(request: Request, task_id: str) -> bool:
    return any(run.graph.task_id == task_id for run in _runs(request).values())


def _scheduler(request: Request) -> _GraphScheduler:
    return request.app.state.task_graph_scheduler


async def _run_graph(run: TaskGraphRun, scheduler: _GraphScheduler) -> None:
    try:
        await scheduler.schedule_graph(run.graph)
    except Exception as error:
        run.error_code = type(error).__name__


def _graph_status(result: TaskGraphResult) -> str:
    if any(reason == "WAITING_APPROVAL" for reason in result.blocked_nodes.values()):
        return "WAITING_APPROVAL"
    if result.finished_at is None:
        return "RUNNING"
    if result.blocked_nodes:
        return "FAILED"
    return "COMPLETED"


def _snapshot(result: TaskGraphResult, graph: TaskGraph) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    for node in graph.nodes:
        execution = result.node_results.get(node.node_id)
        blocked_reason = result.blocked_nodes.get(node.node_id)
        nodes.append(
            {
                "node_id": node.node_id,
                "dependencies": node.dependencies,
                "request_id": node.request.request_id,
                "step_id": node.request.step_id,
                "status": (
                    execution.status.value
                    if execution
                    else "BLOCKED" if blocked_reason else "PENDING"
                ),
                "checkpoint_id": execution.checkpoint_id if execution else None,
                "error_code": execution.error_code if execution else blocked_reason,
                "blocked_reason": blocked_reason,
                "started_at": execution.started_at if execution else None,
                "finished_at": execution.finished_at if execution else None,
            }
        )
    return {
        "graph_id": result.graph_id,
        "task_id": result.task_id,
        "status": _graph_status(result),
        "nodes": nodes,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
    }


async def _result_for_graph(
    request: Request, graph_id: str
) -> tuple[TaskGraphRun, TaskGraphResult]:
    run = _runs(request).get(graph_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown task graph")
    try:
        return run, await _scheduler(request).snapshot(graph_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Unknown task graph") from error


@graph_router.post("")
async def submit_graph(graph: TaskGraph, request: Request) -> APIResponse[dict[str, Any]]:
    runs = _runs(request)
    if graph.graph_id in runs:
        raise HTTPException(status_code=409, detail="Task graph already exists")
    scheduler = _scheduler(request)
    prepare_graph = getattr(scheduler, "prepare_graph", None)
    if callable(prepare_graph):
        try:
            await cast(Awaitable[TaskGraphResult], prepare_graph(graph))
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    run = TaskGraphRun(graph=graph, created_at=datetime.now(UTC))
    runs[graph.graph_id] = run
    run.background = asyncio.create_task(_run_graph(run, scheduler))
    return APIResponse(
        data={
            "graph_id": graph.graph_id,
            "task_id": graph.task_id,
            "status": "RUNNING",
            "created_at": run.created_at,
        }
    )


@graph_router.post("/{graph_id}/cancel")
async def cancel_graph(graph_id: str, request: Request) -> APIResponse[dict[str, Any]]:
    run, _ = await _result_for_graph(request, graph_id)
    try:
        result = await _scheduler(request).cancel_graph(graph_id)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if run.background is not None and not run.background.done():
        await run.background
    return APIResponse(data=_snapshot(result, run.graph))


@graph_router.post("/{graph_id}/resume")
async def resume_graph(
    graph_id: str,
    payload: dict[str, str],
    request: Request,
) -> APIResponse[dict[str, Any]]:
    approval_id = payload.get("approval_id")
    if not approval_id:
        raise HTTPException(status_code=422, detail="approval_id is required")
    run, _ = await _result_for_graph(request, graph_id)
    try:
        result = await _scheduler(request).resume_after_approval(graph_id, approval_id)
    except (RuntimeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return APIResponse(data=_snapshot(result, run.graph))


@task_graph_router.get("/{task_id}/graph")
async def get_graph(task_id: str, request: Request) -> APIResponse[dict[str, Any]]:
    matching = [run for run in _runs(request).values() if run.graph.task_id == task_id]
    if len(matching) != 1:
        raise HTTPException(status_code=404, detail="Unknown task graph")
    run = matching[0]
    try:
        result = await _scheduler(request).snapshot(run.graph.graph_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="Unknown task graph") from error
    return APIResponse(data=_snapshot(result, run.graph))


@task_graph_router.get("/{task_id}/effects")
async def list_effects(
    task_id: str,
    request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[list[dict[str, Any]]]:
    if not is_known_graph_task(request, task_id):
        raise HTTPException(status_code=404, detail="Unknown task")
    if services.effect_store is None:
        return APIResponse(data=[])
    effects = await services.effect_store.list_by_task_id(task_id)
    return APIResponse(
        data=[
            {
                "effect_id": effect.effect_id,
                "kind": effect.kind,
                "target_ref": effect.target_ref,
                "status": effect.status.value,
                "checkpoint_id": effect.checkpoint_id,
                "artifact_refs": effect.artifact_refs,
                "created_at": effect.created_at,
            }
            for effect in effects
        ]
    )


async def approval_view(
    services: ServiceContainer,
    approval: ApprovalRequest,
) -> dict[str, str]:
    decision = await services.approval_service.get_decision(approval.approval_id)
    status = decision.status if decision is not None else approval.status
    return {
        "approval_id": approval.approval_id,
        "task_id": approval.task_id,
        "status": status.value,
        "tool_name": approval.tool_name,
        "reason": approval.reason,
        "step_id": approval.step_id,
        "request_id": approval.request_id,
    }


async def list_approval_views(
    services: ServiceContainer,
    *,
    task_id: str | None = None,
    status: ApprovalStatus | None = None,
) -> list[dict[str, str]]:
    approvals = (
        await services.approval_service.list_for_task(task_id)
        if task_id is not None
        else await services.approval_service.list_all()
    )
    views = [await approval_view(services, approval) for approval in approvals]
    return [view for view in views if status is None or view["status"] == status.value]
