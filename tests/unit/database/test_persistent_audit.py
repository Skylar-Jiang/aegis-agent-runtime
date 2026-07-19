"""Tests for PersistentAuditRecorder backed by in-memory SQLite."""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ra_agent.audit.event_bus import PersistentAuditRecorder
from ra_agent.contracts import AuditEventType
from ra_agent.database.models import Base
from ra_agent.database.repositories.audit import SqliteAuditRepository


def _make_recorder() -> PersistentAuditRecorder:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    repo = SqliteAuditRepository(session_factory)
    return PersistentAuditRecorder(repository=repo)


def _setup_tables() -> None:
    """Run inside an async context before tests that need tables."""
    ...


async def _init_db() -> async_sessionmaker:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _make_recorder_with_db(session_factory: async_sessionmaker) -> PersistentAuditRecorder:
    repo = SqliteAuditRepository(session_factory)
    return PersistentAuditRecorder(repository=repo)


def test_persistent_recorder_owns_monotonic_sequences_across_tasks() -> None:
    async def exercise() -> None:
        sf = await _init_db()
        recorder = _make_recorder_with_db(sf)

        first = await recorder.record(
            task_id="task-1",
            step_id="step-1",
            request_id="request-1",
            event_type=AuditEventType.TOOL_REQUESTED,
            actor="runtime",
            status="recorded",
            summary="first",
        )
        second = await recorder.record(
            task_id="task-1",
            step_id="step-2",
            request_id="request-2",
            event_type=AuditEventType.TOOL_REQUESTED,
            actor="runtime",
            status="recorded",
            summary="second",
        )
        other = await recorder.record(
            task_id="task-2",
            event_type=AuditEventType.TASK_CREATED,
            actor="api",
            status="created",
            summary="other task",
        )

        assert [first.sequence_number, second.sequence_number] == [1, 2]
        assert other.sequence_number == 1
        assert first.event_id.startswith("event-")
        assert first.event_id != second.event_id
        assert first.timestamp.tzinfo is not None

    asyncio.run(exercise())


def test_persistent_recorder_redacts_sensitive_keys() -> None:
    async def exercise() -> None:
        sf = await _init_db()
        recorder = _make_recorder_with_db(sf)

        event = await recorder.record(
            task_id="task-1",
            event_type=AuditEventType.TOOL_REQUESTED,
            actor="runtime",
            status="recorded",
            summary="redaction test",
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


def test_persistent_recorder_concurrent_writes_give_unique_sequences() -> None:
    async def exercise() -> None:
        sf = await _init_db()
        recorder = _make_recorder_with_db(sf)

        # Write 50 events concurrently to the same task
        await asyncio.gather(
            *(
                recorder.record(
                    task_id="task-1",
                    event_type=AuditEventType.TOOL_REQUESTED,
                    actor="runtime",
                    status="recorded",
                    summary=f"event {i}",
                )
                for i in range(50)
            )
        )

        events = await recorder.events_for("task-1", limit=100)
        seqs = [e["sequence_number"] for e in events]
        assert sorted(seqs) == list(range(1, 51))
        assert len(set(seqs)) == 50  # all unique

    asyncio.run(exercise())


def test_persistent_recorder_events_for_returns_ordered_dicts() -> None:
    async def exercise() -> None:
        sf = await _init_db()
        recorder = _make_recorder_with_db(sf)

        await recorder.record(
            task_id="task-A", event_type=AuditEventType.TASK_CREATED,
            actor="api", status="created", summary="A1",
        )
        await recorder.record(
            task_id="task-A", event_type=AuditEventType.EXECUTION_STARTED,
            actor="runtime", status="executing", summary="A2",
        )

        events = await recorder.events_for("task-A")
        assert len(events) == 2
        assert events[0]["sequence_number"] == 1
        assert events[1]["sequence_number"] == 2
        assert events[0]["event_type"] == "TASK_CREATED"
        assert events[1]["event_type"] == "EXECUTION_STARTED"
        assert all(isinstance(e["details"], dict) for e in events)

    asyncio.run(exercise())


def test_persistent_recorder_survives_recreation() -> None:
    async def exercise() -> None:
        sf = await _init_db()
        r1 = _make_recorder_with_db(sf)

        await r1.record(
            task_id="task-survive", event_type=AuditEventType.TASK_CREATED,
            actor="api", status="created", summary="test",
        )

        # Create a new recorder with the same DB
        r2 = _make_recorder_with_db(sf)
        events = await r2.events_for("task-survive")
        assert len(events) == 1
        assert events[0]["sequence_number"] == 1

        # New event should get seq 2
        await r2.record(
            task_id="task-survive", event_type=AuditEventType.TASK_FINISHED,
            actor="runtime", status="finished", summary="done",
        )
        events2 = await r2.events_for("task-survive")
        assert len(events2) == 2
        assert events2[1]["sequence_number"] == 2

    asyncio.run(exercise())


def test_persistent_recorder_subscribe_receives_events() -> None:
    async def exercise() -> None:
        sf = await _init_db()
        recorder = _make_recorder_with_db(sf)

        queue = await recorder.subscribe("task-sub")

        await recorder.record(
            task_id="task-sub", event_type=AuditEventType.TASK_CREATED,
            actor="api", status="created", summary="sub test",
        )

        event = queue.get_nowait()
        assert event.task_id == "task-sub"
        assert event.event_type == AuditEventType.TASK_CREATED

        await recorder.unsubscribe("task-sub", queue)

    asyncio.run(exercise())
