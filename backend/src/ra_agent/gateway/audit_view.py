"""Workbench projection of Core events; signed stored evidence stays unchanged."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any


class CoreAuditView:
    def __init__(self, legacy: Any, core_events: Any) -> None:
        self.legacy = legacy
        self.core_events = core_events
        self._subscribers: dict[str, list[asyncio.Queue[None]]] = {}

    async def record(self, **kwargs: Any) -> Any:
        result = await self.legacy.record(**kwargs)
        # Notifications wake durable replay; legacy numbers must never become a
        # cursor in the independent Core event chain. Coalesce slow subscribers.
        for queue in self._subscribers.get(kwargs["task_id"], []):
            if not queue.full():
                queue.put_nowait(None)
        return result

    async def subscribe(self, task_id: str) -> Any:
        queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        self._subscribers.setdefault(task_id, []).append(queue)
        return queue

    async def unsubscribe(self, task_id: str, queue: Any) -> None:
        queues = self._subscribers.get(task_id, [])
        if queue in queues:
            queues.remove(queue)
        if not queues:
            self._subscribers.pop(task_id, None)

    async def events_for(
        self, task_id: str, *, limit: int = 100, offset: int = 0, after_sequence: int = 0
    ) -> list[dict[str, Any]]:
        page_reader = getattr(self.core_events, "list_task_event_page", None)
        if page_reader is not None:
            page = await page_reader(
                task_id, after_sequence=after_sequence, limit=limit, offset=offset
            )
            if page:
                return [self._project(event) for event in page]
            # An empty page after the last Core event must not switch numbering to
            # the independent legacy trace for the same task.
            if await self.core_events.get_last_event_id(task_id) is not None:
                return []
            events = []
        else:
            events = await self.core_events.list_task_events(task_id)
        if events:
            filtered = [event for event in events if event["sequence"] > after_sequence]
            return [self._project(event) for event in filtered[offset : offset + limit]]
        reader = self.legacy.events_for
        if inspect.iscoroutinefunction(reader):
            parameters = inspect.signature(reader).parameters
            if "after_sequence" in parameters:
                return await reader(
                    task_id, limit=limit, offset=offset, after_sequence=after_sequence
                )
            events = await reader(task_id, limit=limit, offset=offset)
        else:
            from ra_agent.audit.reader import event_data

            events = [event_data(event) for event in reader(task_id)]
            events = [event for event in events if event["sequence_number"] > after_sequence]
            return events[offset : offset + limit]
        return [event for event in events if event["sequence_number"] > after_sequence]

    @staticmethod
    def _project(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "event_id": event["event_id"],
            "task_id": event["task_id"],
            "step_id": None,
            "request_id": event.get("request_id"),
            "sequence_number": event["sequence"],
            "event_type": event["type"],
            "timestamp": event["occurred_at"],
            "actor": event["actor"],
            "status": event["state"],
            "risk_level": None,
            "decision": event.get("decision"),
            "summary": event["type"],
            "details": {
                "source": "core",
                "source_ref": event.get("source_ref"),
                "object_digest": event.get("object_digest"),
                "result_digest": event.get("result_digest"),
                "evidence_refs": event.get("evidence_refs", []),
            },
        }
