import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from ra_agent.audit.reader import event_data, iter_task_events
from ra_agent.core.container import ServiceContainer

from .deps import get_services

router = APIRouter(prefix="/api/tasks", tags=["streams"])


def _sequence(event: dict[str, object]) -> int:
    value = event.get("sequence_number", 0)
    return int(value) if isinstance(value, int | float | str) else 0


def _sse(event: dict[str, object]) -> str:
    sequence = _sequence(event)
    return f"id: {sequence}\nevent: audit\ndata: {json.dumps(event, default=str)}\n\n"


def _assistant_sse(task_id: str, final_answer: str) -> str:
    return (
        "event: assistant\ndata: "
        + json.dumps({"task_id": task_id, "final_answer": final_answer})
        + "\n\n"
    )


def _final_answer(request: Request, task_id: str) -> str | None:
    runs = getattr(request.app.state, "task_runs", {})
    answer = getattr(runs.get(task_id), "final_answer", None)
    return answer if isinstance(answer, str) else None


async def _event_generator(
    task_id: str,
    services: ServiceContainer,
    *,
    last_sequence: int = 0,
    final_answer: Callable[[], str | None] = lambda: None,
) -> AsyncIterator[str]:
    recorder = services.audit_recorder
    subscribe = getattr(recorder, "subscribe", None)
    unsubscribe = getattr(recorder, "unsubscribe", None)
    if subscribe is None:
        sent_sequence = last_sequence
        while True:
            async for event in iter_task_events(recorder, task_id, after_sequence=sent_sequence):
                sent_sequence = _sequence(event)
                yield _sse(event)
                if event.get("event_type") == "TASK_FINISHED":
                    answer = final_answer()
                    if answer is not None:
                        yield _assistant_sse(task_id, answer)
            await asyncio.sleep(2)

    queue = await subscribe(task_id)
    sent_sequence = last_sequence
    try:
        # Subscribing first ensures events arriving during replay remain queued.
        async for event in iter_task_events(recorder, task_id, after_sequence=sent_sequence):
            sent_sequence = _sequence(event)
            yield _sse(event)
            if event.get("event_type") == "TASK_FINISHED":
                answer = final_answer()
                if answer is not None:
                    yield _assistant_sse(task_id, answer)
        while True:
            try:
                queued = await asyncio.wait_for(queue.get(), timeout=2)
            except TimeoutError:
                queued = None
            event = event_data(queued) if queued is not None else None
            if event is not None and _sequence(event) <= sent_sequence:
                continue
            # A subscription is local to its recorder. Replay from the durable
            # cursor as well, so another worker's earlier events are not skipped.
            async for stored in iter_task_events(recorder, task_id, after_sequence=sent_sequence):
                sent_sequence = _sequence(stored)
                yield _sse(stored)
                if stored.get("event_type") == "TASK_FINISHED":
                    answer = final_answer()
                    if answer is not None:
                        yield _assistant_sse(task_id, answer)
            if event is not None and _sequence(event) > sent_sequence:
                sent_sequence = _sequence(event)
                yield _sse(event)
                if event.get("event_type") == "TASK_FINISHED":
                    answer = final_answer()
                    if answer is not None:
                        yield _assistant_sse(task_id, answer)
            elif event is None:
                yield ": keepalive\n\n"
    finally:
        if unsubscribe is not None:
            # ASGI disconnects cancel an AnyIO scope repeatedly at await points;
            # allow cleanup to acquire the recorder lock before propagating it.
            with anyio.CancelScope(shield=True):
                await unsubscribe(task_id, queue)


@router.get("/{task_id}/stream")
async def stream(
    task_id: str,
    request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
    last_event_id: int | None = Header(default=None),
    after_sequence: int = Query(default=0, ge=0),
) -> StreamingResponse:
    return StreamingResponse(
        _event_generator(
            task_id,
            services,
            last_sequence=max(last_event_id or 0, after_sequence),
            final_answer=lambda: _final_answer(request, task_id),
        ),
        media_type="text/event-stream",
    )
