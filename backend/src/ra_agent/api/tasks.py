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
