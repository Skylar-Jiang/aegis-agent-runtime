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
        try:
            events = await recorder.events_for(  # type: ignore[union-attr]
                task_id, limit=500, offset=0
            )
        except TypeError:
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
            ]

    report_data = generate_report(task_id, events=events)
    return APIResponse(data=report_data)
