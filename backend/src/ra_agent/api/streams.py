import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ra_agent.core.container import ServiceContainer

from .deps import get_services

router = APIRouter(prefix="/api/tasks", tags=["streams"])


async def _event_generator(
    task_id: str, services: ServiceContainer
) -> AsyncIterator[str]:
    recorder = services.audit_recorder
    subscribe = getattr(recorder, "subscribe", None)
    unsubscribe = getattr(recorder, "unsubscribe", None)
    if subscribe is not None:
        queue = await subscribe(task_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                payload = json.dumps(
                    {
                        "event_id": event.event_id,
                        "task_id": event.task_id,
                        "event_type": event.event_type.value
                        if hasattr(event.event_type, "value")
                        else str(event.event_type),
                        "timestamp": str(event.timestamp),
                        "actor": event.actor,
                        "status": event.status,
                        "summary": event.summary,
                    },
                    default=str,
                )
                yield f"event: audit\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            if unsubscribe is not None:
                await unsubscribe(task_id, queue)
    else:
        # Fallback for recorders without subscribe (InMemoryAuditRecorder):
        # send existing events, then poll for new ones
        sent_seq = 0
        try:
            while True:
                events: list[dict[str, object]] = []
                if hasattr(recorder, "events_for"):
                    try:
                        events = await recorder.events_for(  # type: ignore[union-attr]
                            task_id, limit=100, offset=0
                        )
                    except TypeError:
                        raw = recorder.events_for(task_id)  # type: ignore[union-attr]
                        events = [
                            {
                                "event_id": e.event_id,
                                "task_id": e.task_id,
                                "event_type": e.event_type.value
                                if hasattr(e.event_type, "value")
                                else str(e.event_type),
                                "timestamp": str(e.timestamp),
                                "actor": e.actor,
                                "status": e.status,
                                "summary": e.summary,
                            }
                            for e in raw
                        ]
                for evt in events:
                    seq_raw = evt.get("sequence_number", 0)
                    seq = int(seq_raw) if isinstance(seq_raw, (int, float, str)) else 0
                    if seq > sent_seq:
                        sent_seq = seq
                        yield f"event: audit\ndata: {json.dumps(evt, default=str)}\n\n"
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            pass


@router.get("/{task_id}/stream")
async def stream(
    task_id: str,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> StreamingResponse:
    return StreamingResponse(
        _event_generator(task_id, services), media_type="text/event-stream"
    )
