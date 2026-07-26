import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ra_agent.agent import AgentRuntime
from ra_agent.contracts import (
    APIResponse,
    AuditEventType,
    TaskContract,
    TaskCreateRequest,
    TaskResponse,
)
from ra_agent.core.container import ServiceContainer
from ra_agent.core.ids import new_id

from .deps import get_agent_runner, get_services

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@dataclass
class TaskRun:
    task_id: str
    objective: str
    created_at: datetime
    status: str = "RUNNING"
    background: asyncio.Task[None] | None = None


def _runs(request: Request) -> dict[str, TaskRun]:
    runs = getattr(request.app.state, "task_runs", None)
    if runs is None:
        runs = {}
        request.app.state.task_runs = runs
    return runs


async def _run_task(
    run: TaskRun,
    agent_runner: AgentRuntime,
    services: ServiceContainer,
    contract: TaskContract | None,
) -> None:
    try:
        state = await agent_runner.run(run.task_id, run.objective, contract)
        run.status = state.status.value
        await services.audit_recorder.record(
            task_id=run.task_id,
            event_type=AuditEventType.TASK_FINISHED,
            actor="api",
            status=run.status,
            summary=f"Task finished: {run.status}",
        )
    except asyncio.CancelledError:
        run.status = "CANCELLED"
        raise
    except Exception as exc:
        run.status = "FAILED"
        await services.audit_recorder.record(
            task_id=run.task_id,
            event_type=AuditEventType.STEP_FAILED,
            actor="api",
            status="FAILED",
            summary="Task background execution failed",
            details={"error": str(exc)},
        )


@router.post("")
async def create_task(
    request: TaskCreateRequest,
    http_request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
    agent_runner: Annotated[AgentRuntime, Depends(get_agent_runner)],
) -> APIResponse[TaskResponse]:
    task_id = new_id("task")
    now = datetime.now(UTC)
    try:
        await services.audit_recorder.record(
            task_id=task_id,
            event_type=AuditEventType.TASK_CREATED,
            actor="api",
            status="CREATED",
            summary=f"Task created: {request.objective}",
            details={"objective": request.objective},
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="task audit persistence unavailable") from exc
    run = TaskRun(task_id=task_id, objective=request.objective, created_at=now)
    _runs(http_request)[task_id] = run
    run.background = asyncio.create_task(_run_task(run, agent_runner, services, request.contract))
    return APIResponse(
        data=TaskResponse(
            task_id=task_id,
            objective=request.objective,
            status=run.status,
            created_at=now,
        )
    )


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    request: Request,
) -> APIResponse[dict[str, str]]:
    run = _runs(request).get(task_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown task")
    return APIResponse(data={"task_id": task_id, "status": run.status})


@router.get("")
async def list_tasks(request: Request) -> APIResponse[list[dict[str, str]]]:
    return APIResponse(
        data=[
            {
                "task_id": run.task_id,
                "objective": run.objective,
                "status": run.status,
                "created_at": run.created_at.isoformat(),
            }
            for run in _runs(request).values()
        ]
    )


@router.get("/{task_id}/approvals")
async def list_task_approvals(
    task_id: str,
    request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[list[dict[str, str]]]:
    if task_id not in _runs(request):
        raise HTTPException(status_code=404, detail="Unknown task")
    approvals = await services.approval_service.list_for_task(task_id)
    return APIResponse(
        data=[
            {
                "approval_id": approval.approval_id,
                "status": approval.status.value,
                "tool_name": approval.tool_name,
                "reason": approval.reason,
                "step_id": approval.step_id,
                "request_id": approval.request_id,
            }
            for approval in approvals
        ]
    )


@router.get("/{task_id}/steps")
async def get_steps(
    task_id: str,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[dict[str, object]]:
    return APIResponse(data={"task_id": task_id, "steps": []})


@router.get("/{task_id}/events")
async def get_events(
    task_id: str,
    services: Annotated[ServiceContainer, Depends(get_services)],
    limit: int = 100,
    offset: int = 0,
) -> APIResponse[dict[str, Any]]:
    recorder = services.audit_recorder
    events: list[dict[str, Any]] = []
    if hasattr(recorder, "events_for"):
        try:
            events = await recorder.events_for(  # type: ignore[union-attr]
                task_id, limit=limit, offset=offset
            )
        except TypeError:
            # Fallback for InMemoryAuditRecorder which only takes task_id
            raw = recorder.events_for(task_id)  # type: ignore[union-attr]
            events = [
                {
                    "event_id": e.event_id,
                    "task_id": e.task_id,
                    "step_id": e.step_id,
                    "request_id": e.request_id,
                    "sequence_number": e.sequence_number,
                    "event_type": e.event_type.value,
                    "timestamp": str(e.timestamp),
                    "actor": e.actor,
                    "status": e.status,
                    "risk_level": e.risk_level.value if e.risk_level else None,
                    "decision": e.decision.value if e.decision else None,
                    "summary": e.summary,
                    "details": e.details,
                }
                for e in raw
            ][offset : offset + limit]
    return APIResponse(
        data={"task_id": task_id, "events": events, "count": len(events)}
    )


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[dict[str, str]]:
    run = _runs(request).get(task_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Unknown task")
    interrupt = getattr(request.app.state.agent_runner, "cancel_task", None)
    if interrupt is not None:
        await interrupt(task_id)
    if run.background is not None and not run.background.done():
        run.background.cancel()
    run.status = "CANCELLED"
    try:
        await services.audit_recorder.record(
            task_id=task_id,
            event_type=AuditEventType.TASK_CANCELLED,
            actor="api",
            status="CANCELLED",
            summary="Task cancelled by user",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="task audit persistence unavailable") from exc
    return APIResponse(data={"task_id": task_id, "status": "CANCELLED"})
