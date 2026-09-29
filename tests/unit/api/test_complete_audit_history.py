import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from types import SimpleNamespace
from typing import cast

import anyio
import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ra_agent.api.reports import report
from ra_agent.api.streams import _event_generator
from ra_agent.audit.event_bus import PersistentAuditRecorder
from ra_agent.audit.reader import iter_task_events
from ra_agent.contracts import AuditEventType
from ra_agent.core.container import ServiceContainer
from ra_agent.database.models import AuditEventRow, AuditTaskSequenceRow, Base
from ra_agent.database.repositories.audit import SqliteAuditRepository
from ra_agent.events import SqliteEventStore
from ra_agent.gateway.audit_view import CoreAuditView
from tests.unit.events.test_sqlite_event_store import event as core_event


@pytest.mark.asyncio
async def test_core_stream_never_uses_legacy_subscription_sequence(history, tmp_path):
    core = SqliteEventStore(tmp_path / "core.sqlite3")
    await core.append_event(core_event())
    view = CoreAuditView(history, core)
    services = cast(ServiceContainer, SimpleNamespace(audit_recorder=view))
    stream = cast(AsyncGenerator[str, None], _event_generator("task", services))
    try:
        assert (await anext(stream)).startswith("id: 1\n")
        for _ in range(3):
            await view.record(
                task_id="task",
                event_type=AuditEventType.CHECKPOINT_CREATED,
                actor="runtime",
                status="RUNNING",
                summary="independent legacy sequence",
            )
        assert await anext(stream) == ": keepalive\n\n"
        for index in (2, 3):
            await core.append_event(
                core_event(index) | {"parent_event_id": f"event-{index - 1}"}
            )
        for index in (2, 3):
            message = await asyncio.wait_for(anext(stream), 3)
            assert message.startswith(f"id: {index}\n")
            assert "GATEWAY_ALLOWED" in message
            assert "CHECKPOINT_CREATED" not in message
    finally:
        await stream.aclose()


@pytest.fixture
async def history(tmp_path) -> AsyncIterator[PersistentAuditRecorder]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'audit.db').as_posix()}"
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await connection.execute(
                insert(AuditEventRow),
                [
                    {
                        "event_id": f"event-{i}",
                        "task_id": "task",
                        "sequence_number": i,
                        "event_type": "TASK_FINISHED"
                        if i == 1105
                        else "TOOL_REQUESTED",
                        "timestamp": "2026-09-23T00:00:00Z",
                        "actor": "runtime",
                        "status": "COMPLETED" if i == 1105 else "RUNNING",
                        "summary": f"event {i}",
                        "details": {},
                    }
                    for i in range(1, 1106)
                ],
            )
            await connection.execute(
                insert(AuditTaskSequenceRow).values(
                    task_id="task",
                    next_sequence_number=1106,
                )
            )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        yield PersistentAuditRecorder(repository=SqliteAuditRepository(sessions))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_replay_reads_after_arbitrary_cursor_beyond_first_thousand(
    history,
):
    events = [
        event async for event in iter_task_events(history, "task", after_sequence=1003)
    ]
    assert [event["sequence_number"] for event in events] == list(range(1004, 1106))


@pytest.mark.asyncio
async def test_report_includes_all_1105_sqlite_events_and_final_status(history):
    services = cast(ServiceContainer, SimpleNamespace(audit_recorder=history))
    response = await report("task", services)
    assert response.data is not None
    assert response.data["total_events"] == 1105
    assert response.data["status"] == "COMPLETED"
    assert response.data["event_summary"] == {
        "TOOL_REQUESTED": 1104,
        "TASK_FINISHED": 1,
    }
    assert [event["sequence_number"] for event in response.data["timeline"]] == list(
        range(1, 1106)
    )


@pytest.mark.asyncio
async def test_stream_close_releases_subscription_after_partial_replay(history):
    services = cast(ServiceContainer, SimpleNamespace(audit_recorder=history))
    generator = cast(AsyncGenerator[str, None], _event_generator("task", services))
    assert (await anext(generator)).startswith("id: 1\n")
    await generator.aclose()
    assert not history._subscribers.get("task")


@pytest.mark.asyncio
async def test_stream_resumes_after_page_boundary_without_duplicates(history):
    services = cast(ServiceContainer, SimpleNamespace(audit_recorder=history))
    generator = cast(
        AsyncGenerator[str, None],
        _event_generator(
            "task",
            services,
            last_sequence=1000,
            final_answer=lambda: "Complete history",
        ),
    )
    try:
        chunks = [
            await asyncio.wait_for(anext(generator), timeout=2) for _ in range(105)
        ]
        assert [int(chunk.splitlines()[0][4:]) for chunk in chunks] == list(
            range(1001, 1106)
        )
        assert "Complete history" in await anext(generator)
        await history.record(
            task_id="task",
            event_type=AuditEventType.TOOL_REQUESTED,
            actor="runtime",
            status="RUNNING",
            summary="new event",
        )
        assert (await asyncio.wait_for(anext(generator), timeout=2)).startswith(
            "id: 1106\n"
        )
    finally:
        await generator.aclose()
    assert not history._subscribers.get("task")


@pytest.mark.asyncio
async def test_stream_replays_other_recorder_events_before_local_queue_event(history):
    other = PersistentAuditRecorder(repository=history._repo)
    services = cast(ServiceContainer, SimpleNamespace(audit_recorder=history))
    generator = cast(
        AsyncGenerator[str, None],
        _event_generator(
            "task",
            services,
            last_sequence=1104,
        ),
    )
    try:
        assert (await anext(generator)).startswith("id: 1105\n")
        for recorder in (other, history):
            await recorder.record(
                task_id="task",
                event_type=AuditEventType.TOOL_REQUESTED,
                actor="runtime",
                status="RUNNING",
                summary="another writer",
            )
        assert (await asyncio.wait_for(anext(generator), timeout=2)).startswith(
            "id: 1106\n"
        )
        assert (await asyncio.wait_for(anext(generator), timeout=2)).startswith(
            "id: 1107\n"
        )
    finally:
        await generator.aclose()
    assert not history._subscribers.get("task")


@pytest.mark.asyncio
async def test_stream_polls_persisted_events_from_other_recorder(history):
    other = PersistentAuditRecorder(repository=history._repo)
    services = cast(ServiceContainer, SimpleNamespace(audit_recorder=history))
    generator = cast(
        AsyncGenerator[str, None],
        _event_generator(
            "task",
            services,
            last_sequence=1104,
        ),
    )
    try:
        assert (await anext(generator)).startswith("id: 1105\n")
        await other.record(
            task_id="task",
            event_type=AuditEventType.TOOL_REQUESTED,
            actor="runtime",
            status="RUNNING",
            summary="another writer",
        )
        assert (await asyncio.wait_for(anext(generator), timeout=4)).startswith(
            "id: 1106\n"
        )
    finally:
        await generator.aclose()


@pytest.mark.asyncio
async def test_cancelled_stream_releases_subscription_and_propagates_cancellation(
    history,
):
    services = cast(ServiceContainer, SimpleNamespace(audit_recorder=history))
    generator = cast(
        AsyncGenerator[str, None],
        _event_generator(
            "task",
            services,
            last_sequence=1104,
        ),
    )
    assert (await anext(generator)).startswith("id: 1105\n")
    waiting = asyncio.create_task(anext(generator))
    await asyncio.sleep(0)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await generator.aclose()
    assert not history._subscribers.get("task")


@pytest.mark.asyncio
async def test_stream_cleanup_survives_asgi_cancel_scope_while_recorder_is_busy(
    history,
):
    services = cast(ServiceContainer, SimpleNamespace(audit_recorder=history))
    ready = anyio.Event()

    async def consume() -> None:
        async for _ in _event_generator("task", services, last_sequence=1104):
            ready.set()

    async with anyio.create_task_group() as group:
        group.start_soon(consume)
        await ready.wait()
        async with history._lock:
            group.cancel_scope.cancel()
            with anyio.CancelScope(shield=True):
                await anyio.sleep(0.01)
    assert not history._subscribers.get("task")
