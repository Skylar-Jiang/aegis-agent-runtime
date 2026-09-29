"""Read complete ordered audit history from durable or in-memory recorders."""

from collections.abc import AsyncIterator
from inspect import iscoroutinefunction, signature
from typing import Any


def event_data(event: Any) -> dict[str, Any]:
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


async def iter_task_events(
    recorder: object, task_id: str, *, after_sequence: int = 0, page_size: int = 500
) -> AsyncIterator[dict[str, Any]]:
    events_for = getattr(recorder, "events_for", None)
    if events_for is None:
        return
    asynchronous = iscoroutinefunction(events_for)
    supports_cursor = asynchronous and "after_sequence" in signature(events_for).parameters
    cursor, offset = after_sequence, 0
    while True:
        if supports_cursor:
            rows = await events_for(task_id, after_sequence=cursor, limit=page_size)
        elif asynchronous:
            rows = await events_for(task_id, limit=page_size, offset=offset)
        else:
            rows = events_for(task_id)
        for item in rows:
            event = event_data(item)
            sequence = int(event["sequence_number"])
            if sequence > cursor:
                cursor = sequence
                yield event
        if len(rows) < page_size or not asynchronous:
            return
        offset += len(rows)
