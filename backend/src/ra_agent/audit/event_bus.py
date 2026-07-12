import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol

from ra_agent.contracts import AuditEvent, AuditEventType, PolicyDecision, RiskLevel
from ra_agent.core.ids import new_id

from .logger import redact


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
