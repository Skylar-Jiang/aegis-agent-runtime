from typing import Protocol

from ra_agent.contracts import AuditEvent


class AuditEventSink(Protocol):
    async def emit(self, event: AuditEvent) -> None: ...


class InMemoryAuditEventSink:
    def __init__(self) -> None:
        self._events: dict[str, list[AuditEvent]] = {}

    async def emit(self, event: AuditEvent) -> None:
        events = self._events.setdefault(event.task_id, [])
        if events and event.sequence_number <= events[-1].sequence_number:
            raise ValueError("Audit sequence_number must increase monotonically per task")
        events.append(event)

    def events_for(self, task_id: str) -> tuple[AuditEvent, ...]:
        return tuple(self._events.get(task_id, []))
