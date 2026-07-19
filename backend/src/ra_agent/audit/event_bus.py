import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol

from ra_agent.contracts import AuditEvent, AuditEventType, PolicyDecision, RiskLevel
from ra_agent.core.ids import new_id

from .logger import redact
from .repository import AuditRepository


class AuditRecorder(Protocol):
    async def record(
        self,
        *,
        task_id: str,
        event_type: AuditEventType,
        actor: str,
        status: str,
        summary: str,
        step_id: str | None = None,
        request_id: str | None = None,
        risk_level: RiskLevel | None = None,
        decision: PolicyDecision | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent: ...


class InMemoryAuditRecorder:
    def __init__(self) -> None:
        self._events: dict[str, list[AuditEvent]] = {}
        self._lock = asyncio.Lock()

    async def record(
        self,
        *,
        task_id: str,
        event_type: AuditEventType,
        actor: str,
        status: str,
        summary: str,
        step_id: str | None = None,
        request_id: str | None = None,
        risk_level: RiskLevel | None = None,
        decision: PolicyDecision | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        async with self._lock:
            events = self._events.get(task_id, [])
            sequence_number = events[-1].sequence_number + 1 if events else 1
            event = AuditEvent(
                event_id=new_id("event"),
                task_id=task_id,
                step_id=step_id,
                request_id=request_id,
                sequence_number=sequence_number,
                event_type=event_type,
                timestamp=datetime.now(UTC),
                actor=actor,
                status=status,
                risk_level=risk_level,
                decision=decision,
                summary=summary,
                details=redact(details or {}),
            )
            self._events.setdefault(task_id, []).append(event)
            return event

    def events_for(self, task_id: str) -> tuple[AuditEvent, ...]:
        return tuple(self._events.get(task_id, []))


class PersistentAuditRecorder:
    """Durable AuditRecorder backed by SQLite via AuditRepository with SSE subscriber support."""

    def __init__(self, *, repository: AuditRepository) -> None:
        self._repo = repository
        self._subscribers: dict[str, list[asyncio.Queue[AuditEvent]]] = {}
        self._lock = asyncio.Lock()

    async def record(
        self,
        *,
        task_id: str,
        event_type: AuditEventType,
        actor: str,
        status: str,
        summary: str,
        step_id: str | None = None,
        request_id: str | None = None,
        risk_level: RiskLevel | None = None,
        decision: PolicyDecision | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        count = await self._repo.count_for_task(task_id)
        sequence_number = count + 1
        event = AuditEvent(
            event_id=new_id("event"),
            task_id=task_id,
            step_id=step_id,
            request_id=request_id,
            sequence_number=sequence_number,
            event_type=event_type,
            timestamp=datetime.now(UTC),
            actor=actor,
            status=status,
            risk_level=risk_level,
            decision=decision,
            summary=summary,
            details=redact(details or {}),
        )
        await self._repo.append(event)
        async with self._lock:
            for queue in self._subscribers.get(task_id, []):
                await queue.put(event)
        return event

    async def subscribe(self, task_id: str) -> asyncio.Queue[AuditEvent]:
        queue: asyncio.Queue[AuditEvent] = asyncio.Queue[AuditEvent]()
        async with self._lock:
            self._subscribers.setdefault(task_id, []).append(queue)
        return queue

    async def unsubscribe(self, task_id: str, queue: asyncio.Queue[AuditEvent]) -> None:
        async with self._lock:
            queues = self._subscribers.get(task_id, [])
            if queue in queues:
                queues.remove(queue)

    async def events_for(
        self, task_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        return await self._repo.list_for_task(task_id, limit=limit, offset=offset)
