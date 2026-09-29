import sqlite3

import pytest

from ra_agent.events import EventStoreError, SqliteEventStore
from tests.unit.events.test_sqlite_event_store import event


@pytest.mark.asyncio
async def test_indexed_page_matches_full_chain_and_checks_page_tampering(tmp_path):
    store = SqliteEventStore(tmp_path / "events.sqlite3")
    for index in range(1, 31):
        await store.append_event(
            event(index)
            | {"parent_event_id": f"event-{index - 1}" if index > 1 else None}
        )
    page = await store.list_task_event_page("task", after_sequence=20, limit=5)
    assert [row["sequence"] for row in page] == [21, 22, 23, 24, 25]
    assert page == (await store.list_task_events("task"))[20:25]
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE core_events SET chain_hash=? WHERE sequence=23", ("0" * 64,)
        )
    with pytest.raises(EventStoreError):
        await store.list_task_event_page("task", after_sequence=20, limit=5)


@pytest.mark.asyncio
async def test_page_accepts_siblings_and_unparented_events_across_boundaries(tmp_path):
    store = SqliteEventStore(tmp_path / "events.sqlite3")
    await store.append_event(event(1))
    await store.append_event(event(2) | {"parent_event_id": "event-1"})
    await store.append_event(event(3) | {"parent_event_id": "event-1"})
    await store.append_event(event(4))
    await store.append_event(event(5) | {"parent_event_id": "event-2"})

    full = await store.list_task_events("task")
    assert await store.list_task_event_page("task", limit=5) == full
    assert await store.list_task_event_page("task", after_sequence=2, limit=2) == full[2:4]
    assert await store.list_task_event_page("task", limit=2, offset=3) == full[3:5]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_parent", ["missing", "other-event", "event-3"])
async def test_page_rejects_missing_cross_task_or_future_parent_even_with_valid_hashes(
    tmp_path, bad_parent
):
    store = SqliteEventStore(tmp_path / "events.sqlite3")
    await store.append_event(event(1))
    await store.append_event(event(2))
    await store.append_event(event(3))
    await store.append_event(event(9, "other") | {"event_id": "other-event"})
    rows = await store.list_task_events("task")
    rows[1]["parent_event_id"] = bad_parent
    previous = "0" * 64
    with sqlite3.connect(store.path) as connection:
        for row in rows:
            entry = store._entry(row, previous)
            connection.execute(
                "UPDATE core_events SET parent_event_id=?, event_json=?, prev_hash=?, "
                "event_digest=?, chain_hash=? WHERE task_id='task' AND sequence=?",
                (
                    row["parent_event_id"],
                    store._canonical.canonicalize(row),
                    entry["prev_hash"],
                    entry["event_digest"],
                    entry["chain_hash"],
                    row["sequence"],
                ),
            )
            previous = entry["chain_hash"]
        connection.execute("UPDATE task_heads SET chain_hash=? WHERE task_id='task'", (previous,))
    with pytest.raises(EventStoreError) as error:
        await store.list_task_event_page("task", limit=3)
    assert error.value.code == "LOG_INVALID"
