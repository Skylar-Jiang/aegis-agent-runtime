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
        ready = json.dumps({"task_id": task_id, "status": "stream_ready"})
        yield f"event: audit\ndata: {ready}\n\n"


@router.get("/{task_id}/stream")
async def stream(
    task_id: str,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> StreamingResponse:
    return StreamingResponse(
        _event_generator(task_id, services), media_type="text/event-stream"
    )
