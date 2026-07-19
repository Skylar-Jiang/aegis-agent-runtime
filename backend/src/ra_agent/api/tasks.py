from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from ra_agent.contracts import (
    APIResponse,
    AuditEventType,
    TaskCreateRequest,
    TaskResponse,
)
from ra_agent.core.container import ServiceContainer
from ra_agent.core.ids import new_id

from .deps import get_services

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("")
async def create_task(
    request: TaskCreateRequest,
    services: Annotated[ServiceContainer, Depends(get_services)],
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
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return APIResponse(
        data=TaskResponse(
            task_id=task_id,
            objective=request.objective,
            status="CREATED",
            created_at=now,
        )
    )


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[dict[str, str]]:
    return APIResponse(data={"task_id": task_id, "status": "active"})


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
        events = await recorder.events_for(  # type: ignore[union-attr]
            task_id, limit=limit, offset=offset
        )
    return APIResponse(
        data={"task_id": task_id, "events": events, "count": len(events)}
    )


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[dict[str, str]]:
    try:
        await services.audit_recorder.record(
            task_id=task_id,
            event_type=AuditEventType.TASK_CANCELLED,
            actor="api",
            status="CANCELLED",
            summary="Task cancelled by user",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return APIResponse(data={"task_id": task_id, "status": "CANCELLED"})
