import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from ra_agent.audit import HashChain
from ra_agent.events import EventStoreError, JsonlEventStore, SqliteEventStore


def event(index=1, task_id="task"):
    return {
        "event_id": f"event-{index}",
        "task_id": task_id,
        "parent_event_id": None,
        "type": "GATEWAY_ALLOWED",
        "actor": "gateway",
        "source_ref": "request",
        "object_digest": None,
        "state": "ALLOWED",
        "decision": "ALLOW",
        "result_digest": None,
        "occurred_at": "2026-09-23T00:00:00Z",
    }


@pytest.fixture
def store_class():
    import ra_agent.events as events

    assert hasattr(events, "SqliteEventStore"), (
        "default Core event store needs indexed durable appends"
    )
    return events.SqliteEventStore


def test_indexed_event_store_is_available_for_core_runtime():
    import ra_agent.events as events

    assert hasattr(events, "SqliteEventStore")


@pytest.mark.asyncio
async def test_restart_and_hash_chain_are_protocol_compatible(store_class, tmp_path):
    path = tmp_path / "events.sqlite3"
    store = store_class(path)
    original = event()
    first = await store.append_event(original)
    assert "sequence" not in original and first["sequence"] == 1
    first["state"] = "MUTATED"
    await store.append_event(event(2) | {"parent_event_id": "event-1"})
    await store.append_event(event(1, "other"))
    reopened = store_class(path)
    rows = await reopened.list_task_events("task")
    assert [row["sequence"] for row in rows] == [1, 2]
    assert rows[0]["state"] == "ALLOWED"
    chain = HashChain("task")
    for row in rows:
        chain.append_event(row)
    assert await reopened.get_chain_head("task") == chain.get_chain_head()
    assert await reopened.list_task_events("missing") == []
    assert await reopened.get_chain_head("missing") == "0" * 64


@pytest.mark.asyncio
async def test_snapshot_keeps_events_and_head_together_during_concurrent_append(tmp_path):
    path = tmp_path / "events.sqlite3"
    reader = SqliteEventStore(path)
    writer = SqliteEventStore(path)
    first = await writer.append_event(event())
    original_replay = reader._replay_task
    reading = threading.Event()
    resume = threading.Event()

    def pause_after_read_transaction(db, task_id, tail):
        reading.set()
        assert resume.wait(5), "snapshot read did not resume"
        return original_replay(db, task_id, tail)

    reader._replay_task = pause_after_read_transaction
    snapshot_task = asyncio.create_task(reader.get_task_snapshot("task"))
    try:
        assert await asyncio.to_thread(reading.wait, 5)
        await asyncio.wait_for(writer.append_event(event(2)), 5)
    finally:
        resume.set()
    events, head = await snapshot_task
    chain = HashChain("task")
    chain.append_event(first)
    assert events == [first]
    assert head == chain.get_chain_head()
    assert len(await writer.list_task_events("task")) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("store_kind", ["sqlite", "jsonl"])
async def test_cancelled_append_waits_for_worker_commit(tmp_path, store_kind):
    store = (
        SqliteEventStore(tmp_path / "events.sqlite3")
        if store_kind == "sqlite"
        else JsonlEventStore(tmp_path / "events.jsonl")
    )
    started = threading.Event()
    release = threading.Event()
    original = store._append

    def paused_append(event):
        started.set()
        assert release.wait(5), "append worker did not resume"
        return original(event)

    store._append = paused_append
    operation = asyncio.create_task(store.append_event(event()))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done(), "append caller released ownership before worker settled"
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    assert [row["event_id"] for row in await store.list_task_events("task")] == ["event-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,code",
    [
        ({"event_id": "event-1"}, "EVENT_ID_CONFLICT"),
        ({"sequence": 9}, "SEQUENCE_INVALID"),
        ({"sequence": True}, "EVENT_INVALID"),
        ({"parent_event_id": "absent"}, "PARENT_EVENT_MISSING"),
        ({"actor": " "}, "EVENT_INVALID"),
        ({"result_digest": "sha256:old"}, "EVENT_INVALID"),
    ],
)
async def test_invalid_append_rolls_back(store_class, tmp_path, changes, code):
    store = store_class(tmp_path / "events.sqlite3")
    await store.append_event(event())
    previous = await store.get_chain_head("task")
    with pytest.raises(EventStoreError) as error:
        await store.append_event(event(2) | changes)
    assert error.value.code == code
    assert await store.get_chain_head("task") == previous
    assert len(await store.list_task_events("task")) == 1


@pytest.mark.asyncio
async def test_concurrent_instances_keep_unique_sequence_and_sibling_parents(store_class, tmp_path):
    path = tmp_path / "events.sqlite3"
    stores = [store_class(path) for _ in range(4)]
    await stores[0].append_event(event())
    await asyncio.gather(
        *[
            stores[i % 4].append_event(event(i + 2) | {"parent_event_id": "event-1"})
            for i in range(32)
        ]
    )
    rows = await stores[0].list_task_events("task")
    assert [row["sequence"] for row in rows] == list(range(1, 34))
    assert len({row["event_id"] for row in rows}) == 33
    assert {row["parent_event_id"] for row in rows[1:]} == {"event-1"}
    with pytest.raises(EventStoreError) as error:
        await stores[0].append_event(event(1, "other") | {"parent_event_id": "event-1"})
    assert error.value.code == "PARENT_EVENT_MISSING"


@pytest.mark.asyncio
async def test_process_writers_do_not_lose_events(store_class, tmp_path):
    path = tmp_path / "events.sqlite3"
    script = """
import asyncio, json, sys
from pathlib import Path
from ra_agent.events import SqliteEventStore
async def main():
    store = SqliteEventStore(Path(sys.argv[1]))
    template = json.loads(sys.argv[3])
    for i in range(10):
        await store.append_event(template | {"event_id": f"event-{sys.argv[2]}-{i}"})
asyncio.run(main())
"""

    def run(index):
        return subprocess.run(
            [sys.executable, "-c", script, str(path), str(index), json.dumps(event())],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[3] / "backend/src")},
        )

    def run_all():
        with ThreadPoolExecutor(max_workers=3) as pool:
            return list(pool.map(run, range(3)))

    results = await asyncio.to_thread(run_all)
    assert all(result.returncode == 0 for result in results), [r.stderr for r in results]
    store = store_class(path)
    rows = await store.list_task_events("task")
    assert len(rows) == 30
    assert [row["sequence"] for row in rows] == list(range(1, 31))


@pytest.mark.asyncio
async def test_legacy_import_is_once_verified_and_non_destructive(store_class, tmp_path):
    source = tmp_path / "events.jsonl"
    legacy = JsonlEventStore(source, chain_factory=HashChain)
    await legacy.append_event(event())
    await legacy.append_event(event(1, "other"))
    await legacy.append_event(event(2) | {"parent_event_id": "event-1"})
    before = source.read_bytes()
    expected = await legacy.list_task_events("task")
    head = await legacy.get_chain_head("task")
    target = tmp_path / "events.sqlite3"
    store = store_class(target, legacy_path=source)
    assert source.read_bytes() == before
    assert await store.list_task_events("task") == expected
    assert await store.get_chain_head("task") == head
    await store.append_event(event(3))
    source.write_bytes(b"old file must never be imported again\n")
    reopened = store_class(target, legacy_path=source)
    assert len(await reopened.list_task_events("task")) == 3


@pytest.mark.asyncio
async def test_import_over_16_mib_has_no_global_log_cap(store_class, tmp_path):
    source = tmp_path / "events.jsonl"
    legacy = JsonlEventStore(source, chain_factory=HashChain)
    await legacy.append_event(event())
    await legacy.append_event(event(1, "other"))
    lines = source.read_bytes().splitlines()
    # Legal JSON whitespace makes an 18 MiB legacy input with tiny events;
    # stream it without weakening per-record validation or allocating the whole log.
    padded = b"".join(line + b" " * (3 * 1024**2) + b"\n" for line in lines)
    # Six independent tasks keep every line below the bounded import line size.
    for i in range(2, 6):
        record = json.loads(lines[0])
        normalized = dict(record["event"], task_id=f"task-{i}")
        record["event"] = normalized
        record["chain"] = HashChain(f"task-{i}").append_event(normalized)
        padded += json.dumps(record).encode() + b" " * (3 * 1024**2) + b"\n"
    source.write_bytes(padded)
    assert source.stat().st_size > 16 * 1024**2
    store = store_class(tmp_path / "events.sqlite3", legacy_path=source)
    assert len(await store.list_task_events("task")) == 1
    assert len(await store.list_task_events("task-5")) == 1
    await store.append_event(event(2))


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["chain", "sequence", "parent", "unchained", "incomplete"])
async def test_failed_import_rolls_back_and_refuses_startup(store_class, tmp_path, damage):
    source = tmp_path / "events.jsonl"
    legacy = JsonlEventStore(source, chain_factory=HashChain)
    await legacy.append_event(event())
    await legacy.append_event(event(2))
    good = source.read_bytes()
    rows = [json.loads(line) for line in good.splitlines()]
    if damage == "chain":
        rows[-1]["chain"]["chain_hash"] = "f" * 64
    elif damage == "sequence":
        rows[-1]["event"]["sequence"] = 9
    elif damage == "parent":
        rows[-1]["event"]["parent_event_id"] = "absent"
    elif damage == "unchained":
        rows[-1]["chain"] = None
    bad = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
    if damage == "incomplete":
        bad = bad[:-1]
    source.write_bytes(bad)
    target = tmp_path / "events.sqlite3"
    with pytest.raises(EventStoreError):
        store_class(target, legacy_path=source)
    assert source.read_bytes() == bad
    source.write_bytes(good)
    recovered = store_class(target, legacy_path=source)
    assert len(await recovered.list_task_events("task")) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["event", "digest", "chain", "middle", "head", "index"])
async def test_task_replay_detects_database_tampering(store_class, tmp_path, damage):
    path = tmp_path / "events.sqlite3"
    store = store_class(path)
    for index in range(1, 4):
        await store.append_event(event(index))
    connection = sqlite3.connect(path)
    try:
        with connection:
            if damage == "event":
                connection.execute("UPDATE core_events SET event_json = '{}' WHERE sequence = 1")
            elif damage in {"digest", "chain"}:
                column = "event_digest" if damage == "digest" else "chain_hash"
                connection.execute(
                    f"UPDATE core_events SET {column} = ? WHERE sequence = 1", ("f" * 64,)
                )
            elif damage == "middle":
                connection.execute("DELETE FROM core_events WHERE sequence = 2")
            elif damage == "head":
                connection.execute("UPDATE task_heads SET chain_hash = ?", ("f" * 64,))
            else:
                connection.execute("UPDATE core_events SET event_id = 'forged' WHERE sequence = 1")
    finally:
        connection.close()
    with pytest.raises(EventStoreError) as error:
        await store.list_task_events("task")
    assert error.value.code == "LOG_INVALID"


@pytest.mark.asyncio
async def test_reads_and_appends_do_not_load_unrelated_task_histories(store_class, tmp_path):
    path = tmp_path / "events.sqlite3"
    store = store_class(path)
    await store.append_event(event())
    await store.append_event(event(1, "other"))
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute("UPDATE core_events SET event_json = '{}' WHERE task_id = 'other'")
    finally:
        connection.close()
    await store.append_event(event(2))
    assert len(await store.list_task_events("task")) == 2
    with pytest.raises(EventStoreError):
        await store.get_chain_head("other")
    with pytest.raises(EventStoreError):
        await store.append_event(event(2, "other"))


@pytest.mark.asyncio
async def test_database_can_append_and_replay_beyond_old_16_mib_limit(store_class, tmp_path):
    path = tmp_path / "events.sqlite3"
    store = store_class(path)
    references = ["x" * 500] * 100
    for index in range(360):
        await store.append_event(event(index) | {"evidence_refs": references})
    assert path.stat().st_size > 16 * 1024**2
    reopened = store_class(path)
    last = await reopened.append_event(event(360))
    assert last["sequence"] == 361
    rows = await reopened.list_task_events("task")
    assert len(rows) == 361 and rows[0]["evidence_refs"] == references


@pytest.mark.asyncio
async def test_crypto_environment_failure_keeps_unavailable_error(
    store_class, tmp_path, monkeypatch
):
    from ra_agent.crypto import CryptoError, DigestProvider

    store = store_class(tmp_path / "events.sqlite3")
    await store.append_event(event())

    def unavailable(*args, **kwargs):
        raise CryptoError("CHECK_UNAVAILABLE")

    monkeypatch.setattr(DigestProvider, "sm3", unavailable)
    with pytest.raises(EventStoreError) as error:
        await store.get_chain_head("task")
    assert error.value.code == "CHECK_UNAVAILABLE"


@pytest.mark.asyncio
async def test_last_event_id_supports_indexed_gateway_parent_lookup(store_class, tmp_path):
    path = tmp_path / "events.sqlite3"
    store = store_class(path)
    await store.append_event(event())
    await store.append_event(event(2))
    assert callable(getattr(store, "get_last_event_id", None))
    assert await store.get_last_event_id("task") == "event-2"
    assert await store.get_last_event_id("missing") is None
    connection = sqlite3.connect(path)
    try:
        with connection:
            connection.execute("UPDATE core_events SET event_id = 'forged' WHERE sequence = 2")
    finally:
        connection.close()
    with pytest.raises(EventStoreError, match="disagree"):
        await store.get_last_event_id("task")
