import asyncio

import ra_agent.audit as audit_module
from ra_agent.audit import InMemoryAuditRecorder
from ra_agent.contracts import AuditEventType


def test_audit_public_api_only_exposes_record() -> None:
    recorder = InMemoryAuditRecorder()

    assert not hasattr(audit_module, "AuditEventSink")
    assert not hasattr(audit_module, "InMemoryAuditEventSink")
    assert not hasattr(recorder, "emit")


def test_recorder_owns_monotonic_sequences_across_requests_and_tasks() -> None:
    async def exercise() -> None:
        recorder = InMemoryAuditRecorder()
        first = await recorder.record(
            task_id="task-1",
            step_id="step-1",
            request_id="request-1",
            event_type=AuditEventType.TOOL_REQUESTED,
            actor="runtime",
            status="recorded",
            summary="first request",
        )
        second = await recorder.record(
            task_id="task-1",
            step_id="step-2",
            request_id="request-2",
            event_type=AuditEventType.TOOL_REQUESTED,
            actor="runtime",
            status="recorded",
            summary="second request",
        )
        other = await recorder.record(
            task_id="task-2",
            event_type=AuditEventType.TOOL_REQUESTED,
            actor="runtime",
            status="recorded",
            summary="other task",
        )

        assert [first.sequence_number, second.sequence_number] == [1, 2]
        assert other.sequence_number == 1
        assert first.event_id != second.event_id
        assert first.timestamp.tzinfo is not None

    asyncio.run(exercise())


def test_recorder_recursively_redacts_sensitive_dicts_and_lists() -> None:
    async def exercise() -> None:
        recorder = InMemoryAuditRecorder()
        event = await recorder.record(
            task_id="task-1",
            event_type=AuditEventType.TOOL_REQUESTED,
            actor="runtime",
            status="recorded",
            summary="redaction",
            details={
                "authorization": "Bearer secret",
                "nested": {
                    "api_key": "key",
                    "items": [{"refresh_token": "refresh"}, {"safe": "visible"}],
                },
            },
        )

        assert event.details == {
            "authorization": "***REDACTED***",
            "nested": {
                "api_key": "***REDACTED***",
                "items": [
                    {"refresh_token": "***REDACTED***"},
                    {"safe": "visible"},
                ],
            },
        }

    asyncio.run(exercise())


def test_concurrent_records_have_unique_continuous_sequences_per_task() -> None:
    async def exercise() -> None:
        recorder = InMemoryAuditRecorder()
        await asyncio.gather(
            *(
                recorder.record(
                    task_id="task-1",
                    event_type=AuditEventType.TOOL_REQUESTED,
                    actor="runtime",
                    status="recorded",
                    summary=f"event {index}",
                )
                for index in range(100)
            ),
            *(
                recorder.record(
                    task_id="task-2",
                    event_type=AuditEventType.TOOL_REQUESTED,
                    actor="runtime",
                    status="recorded",
                    summary=f"event {index}",
                )
                for index in range(20)
            ),
        )

        first = [event.sequence_number for event in recorder.events_for("task-1")]
        second = [event.sequence_number for event in recorder.events_for("task-2")]
        assert first == list(range(1, 101))
        assert second == list(range(1, 21))

    asyncio.run(exercise())
