from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ra_agent.contracts import APIResponse
from ra_agent.core.container import ServiceContainer

from .deps import get_services

router = APIRouter(prefix="/api/tasks", tags=["reports"])


@router.get("/{task_id}/report")
async def report(
    task_id: str,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[dict[str, Any]]:
    recorder = services.audit_recorder
    events: list[dict[str, Any]] = []
    if hasattr(recorder, "events_for"):
        events = await recorder.events_for(  # type: ignore[union-attr]
            task_id, limit=500, offset=0
        )

    event_types: dict[str, int] = {}
    for evt in events:
        et = str(evt.get("event_type", ""))
        event_types[et] = event_types.get(et, 0) + 1

    return APIResponse(
        data={
            "task_id": task_id,
            "total_events": len(events),
            "event_summary": event_types,
        }
    )
