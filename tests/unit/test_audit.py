import asyncio
from datetime import UTC, datetime

import pytest

from ra_agent.audit import InMemoryAuditEventSink
from ra_agent.contracts import AuditEvent, AuditEventType


def make_event(sequence_number: int) -> AuditEvent:
    return AuditEvent(
        event_id=f"event-{sequence_number}",
        task_id="task-1",
        step_id="step-1",
        request_id="request-1",
        sequence_number=sequence_number,
        event_type=AuditEventType.TOOL_REQUESTED,
        timestamp=datetime.now(UTC),
        actor="runtime",
        status="received",
        summary="tool request received",
    )


def test_audit_sequence_is_monotonic_per_task() -> None:
    async def exercise() -> None:
        sink = InMemoryAuditEventSink()
        await sink.emit(make_event(1))

        with pytest.raises(ValueError, match="monotonically"):
            await sink.emit(make_event(1))

    asyncio.run(exercise())
