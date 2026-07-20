import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from ra_agent.core.container import ServiceContainer

from .deps import get_services

router = APIRouter(prefix="/api/tasks", tags=["streams"])


def _event_data(event: Any) -> dict[str, object]:
    if isinstance(event, dict):
        return event
    return {
        "event_id": event.event_id,
        "task_id": event.task_id,
        "step_id": event.step_id,
        "request_id": event.request_id,
        "sequence_number": event.sequence_number,
        "event_type": event.event_type.value,
        "timestamp": str(event.timestamp),
        "actor": event.actor,
        "status": event.status,
        "risk_level": event.risk_level.value if event.risk_level else None,
        "decision": event.decision.value if event.decision else None,
        "summary": event.summary,
        "details": event.details,
    }


def _sequence(event: dict[str, object]) -> int:
    value = event.get("sequence_number", 0)
    return int(value) if isinstance(value, int | float | str) else 0


def _sse(event: dict[str, object]) -> str:
    sequence = _sequence(event)
    return f"id: {sequence}\nevent: audit\ndata: {json.dumps(event, default=str)}\n\n"


async def _events_for(
    recorder: object, task_id: str, *, after_sequence: int
) -> list[dict[str, object]]:
    events_for = getattr(recorder, "events_for", None)
    if events_for is None:
        return []
    try:
        events = await events_for(task_id, limit=1000, offset=0)
    except TypeError:
        events = events_for(task_id)
    result: list[dict[str, object]] = []
    for item in events:
        event = _event_data(item)
        if _sequence(event) > after_sequence:
            result.append(event)
    return result


async def _event_generator(
    task_id: str, services: ServiceContainer, *, last_sequence: int = 0
) -> AsyncIterator[str]:
    recorder = services.audit_recorder
    subscribe = getattr(recorder, "subscribe", None)
    unsubscribe = getattr(recorder, "unsubscribe", None)
    if subscribe is None:
        sent_sequence = last_sequence
        while True:
            for event in await _events_for(recorder, task_id, after_sequence=sent_sequence):
                sent_sequence = _sequence(event)
                yield _sse(event)
            await asyncio.sleep(2)

    queue = await subscribe(task_id)
    sent_sequence = last_sequence
    try:
        # Subscribing first ensures events arriving during replay remain queued.
        for event in await _events_for(recorder, task_id, after_sequence=sent_sequence):
            sent_sequence = _sequence(event)
            yield _sse(event)
        while True:
            try:
                queued = await asyncio.wait_for(queue.get(), timeout=30)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            event = _event_data(queued)
            if _sequence(event) <= sent_sequence:
                continue
            sent_sequence = _sequence(event)
            yield _sse(event)
    except asyncio.CancelledError:
        if unsubscribe is not None:
            await unsubscribe(task_id, queue)


@router.get("/{task_id}/stream")
async def stream(
    task_id: str,
    services: Annotated[ServiceContainer, Depends(get_services)],
    last_event_id: int | None = Header(default=None),
) -> StreamingResponse:
    return StreamingResponse(
        _event_generator(task_id, services, last_sequence=last_event_id or 0),
        media_type="text/event-stream",
    )
