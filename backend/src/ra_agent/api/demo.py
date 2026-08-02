"""Opt-in, local-only fixtures for reproducible browser demonstrations."""

import asyncio
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from ra_agent.contracts import (
    APIResponse,
    PostCheckResult,
    SourceType,
    TaskContract,
    TaskGraph,
    TaskNode,
    ToolCallRequest,
)
from ra_agent.core.container import ServiceContainer
from ra_agent.core.ids import new_id
from ra_agent.tools.implementations.memory_tools import MemoryWriteHandler

from .deps import get_services
from .task_graphs import TaskGraphRun, _run_graph, _runs, _scheduler

router = APIRouter(prefix="/api/demo", tags=["demo"])


def _memory_request(task_id: str, graph_id: str, step_id: str, key: str) -> ToolCallRequest:
    return ToolCallRequest(
        task_id=task_id,
        step_id=step_id,
        request_id=new_id("demo-request"),
        tool_name="memory_write",
        arguments={"key": key, "value": "local demo fixture"},
        objective="demonstrate controlled selective rollback",
        context_summary="local fixture without untrusted content",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
        task_contract=TaskContract(
            allowed_actions=["memory_write", "delete_file"],
            allowed_resources=[key, "demo-gate.txt"],
            max_affected_objects=2,
        ),
    )


@router.post("/selective-rollback")
async def create_selective_rollback_demo(
    request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[dict[str, str]]:
    if not getattr(request.app.state, "enable_demo_fixtures", False):
        raise HTTPException(status_code=404, detail="Not found")
    effect_manager = getattr(services, "effect_manager", None)
    memory_manager = getattr(services, "memory_manager", None)
    if effect_manager is None or memory_manager is None:
        raise HTTPException(status_code=409, detail="Live demo fixtures require managed effects")

    task_id = new_id("demo-task")
    graph_id = new_id("demo-graph")
    committed_request = _memory_request(task_id, graph_id, "independent", "demo-independent")
    pending_request = _memory_request(task_id, graph_id, "affected", "demo-affected")
    handler = MemoryWriteHandler(memory_manager.store)
    committed_execution = await handler(committed_request)
    await effect_manager.register_pending(committed_request, committed_execution)
    await memory_manager.commit(
        committed_request,
        committed_execution,
        PostCheckResult(
            request_id=committed_request.request_id,
            passed=True,
            reason="local fixture post-check passed",
        ),
    )
    pending_execution = await handler(pending_request)
    await effect_manager.register_pending(pending_request, pending_execution)

    approval_request = ToolCallRequest(
        task_id=task_id,
        step_id="approval-gate",
        request_id=new_id("demo-request"),
        tool_name="delete_file",
        arguments={"path": "demo-gate.txt"},
        objective="hold graph at an approval gate until cancellation",
        context_summary="local fixture without untrusted content",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
        task_contract=TaskContract(
            allowed_actions=["delete_file"],
            allowed_resources=["demo-gate.txt"],
            max_affected_objects=1,
        ),
    )
    graph = TaskGraph(
        graph_id=graph_id,
        task_id=task_id,
        max_parallelism=1,
        nodes=[
            TaskNode(
                task_id=task_id,
                graph_id=graph_id,
                node_id="approval-gate",
                request=approval_request,
                parallel_safe=True,
                effect_targets=["file:demo-gate.txt"],
            )
        ],
    )
    scheduler = _scheduler(request)
    await scheduler.prepare_graph(graph)
    run = TaskGraphRun(graph=graph, created_at=datetime.now(UTC))
    _runs(request)[graph_id] = run
    run.background = asyncio.create_task(_run_graph(run, scheduler))
    await run.background
    approvals = await services.approval_service.list_for_task(task_id)
    if len(approvals) != 1:
        raise HTTPException(status_code=500, detail="Demo approval was not created")
    return APIResponse(
        data={
            "task_id": task_id,
            "graph_id": graph_id,
            "approval_id": approvals[0].approval_id,
        }
    )
