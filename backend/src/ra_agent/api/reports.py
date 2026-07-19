from typing import Annotated, Any

from fastapi import APIRouter, Depends

from ra_agent.audit.report_generator import generate_report
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

    report_data = generate_report(task_id, events=events)
    return APIResponse(data=report_data)
