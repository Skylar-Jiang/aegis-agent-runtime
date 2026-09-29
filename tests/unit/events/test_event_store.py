import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ra_agent.events import BehaviorEvent, EventStoreError, JsonlEventStore
from ra_agent.gateway.fakes import InMemoryEventStore
from ra_agent.gateway.interfaces import EventStore

FIXTURE = Path(__file__).parents[2] / "fixtures/core/behavior_events.json"


def event(index: int = 1, task_id: str = "task-1") -> dict[str, Any]:
    return {
        "event_id": f"event-{index}",
        "task_id": task_id,
        "parent_event_id": None,
        "type": "GATEWAY_EVALUATED",
        "actor": "fixture-gateway",
        "source_ref": "fixture:test",
        "object_digest": None,
        "state": "ALLOW",
        "decision": "ALLOW",
        "result_digest": None,
        "occurred_at": "2026-09-19T08:00:00Z",
        "request_id": f"request-{index}",
        "evidence_refs": ["fixture:permission"],
    }


@pytest.mark.parametrize("backend", ["memory", "jsonl"])
async def test_shared_event_store_contract(tmp_path: Path, backend: str) -> None:
    store: EventStore = (
        InMemoryEventStore()
        if backend == "memory"
        else JsonlEventStore(tmp_path / "events.jsonl")
    )
    assert await store.list_task_events("missing") == []
    assert await store.get_chain_head("missing") is None
    original = event()
    stored = await store.append_event(original)
    assert stored["sequence"] == 1
    assert "sequence" not in original
    original["evidence_refs"].append("changed-input")
    stored["evidence_refs"].append("changed-output")
    await store.append_event(event(2))
    await store.append_event(event(1, "task-2"))
    records = await store.list_task_events("task-1")
    assert [r["sequence"] for r in records] == [1, 2]
    assert records[0]["evidence_refs"] == ["fixture:permission"]
    records[0]["state"] = "changed-list"
    assert (await store.list_task_events("task-1"))[0]["state"] == "ALLOW"
    assert len(await store.list_task_events("task-2")) == 1


async def test_fixture_restart_and_nullable_facts(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store = JsonlEventStore(path)
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for item in fixture:
        await store.append_event(item)
    restarted = JsonlEventStore(path)
    records = await restarted.list_task_events("p2-task-1")
    assert records == [
        BehaviorEvent.model_validate(e).model_dump(mode="json") for e in fixture
    ]
    with pytest.raises(EventStoreError, match="No cryptographic") as error:
        await restarted.get_chain_head("p2-task-1")
    assert error.value.code == "CHECK_UNAVAILABLE"


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"event_id": "event-1"}, "EVENT_ID_CONFLICT"),
        ({"sequence": 7}, "SEQUENCE_INVALID"),
        ({"sequence": True}, "EVENT_INVALID"),
        ({"sequence": "2"}, "EVENT_INVALID"),
        ({"parent_event_id": "absent"}, "PARENT_EVENT_MISSING"),
        ({"actor": " "}, "EVENT_INVALID"),
        ({"decision": "FAST_EXECUTE"}, "EVENT_INVALID"),
        ({"occurred_at": "2026-09-19T08:00:00"}, "EVENT_INVALID"),
        ({"occurred_at": 123}, "EVENT_INVALID"),
        ({"result_digest": "sha256:old-value"}, "EVENT_INVALID"),
        ({"raw_result": "must not be persisted"}, "EVENT_INVALID"),
    ],
)
async def test_invalid_append_does_not_change_log(
    tmp_path: Path, changes: dict, code: str
) -> None:
    path = tmp_path / "events.jsonl"
    store = JsonlEventStore(path)
    await store.append_event(event())
    previous = path.read_bytes()
    with pytest.raises(EventStoreError) as error:
        await store.append_event({**event(2), **changes})
    assert error.value.code == code
    assert path.read_bytes() == previous


async def test_parent_cannot_refer_to_another_task(tmp_path: Path) -> None:
    store = JsonlEventStore(tmp_path / "events.jsonl")
    await store.append_event(event())
    with pytest.raises(EventStoreError) as error:
        await store.append_event({**event(2, "task-2"), "parent_event_id": "event-1"})
    assert error.value.code == "PARENT_EVENT_MISSING"


async def test_concurrent_instances_allocate_unique_sequences(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    stores = [JsonlEventStore(path), JsonlEventStore(path)]
    await asyncio.gather(*(stores[i % 2].append_event(event(i)) for i in range(24)))
    rows = await stores[0].list_task_events("task-1")
    assert [r["sequence"] for r in rows] == list(range(1, 25))
    assert len({r["event_id"] for r in rows}) == 24


@pytest.mark.parametrize(
    "damage", [b'{"broken":', b"{}\n", b"\xff\n", b'{"event":{},"event":{}}\n']
)
async def test_corrupted_log_fails_closed(tmp_path: Path, damage: bytes) -> None:
    path = tmp_path / "events.jsonl"
    path.write_bytes(damage)
    store = JsonlEventStore(path)
    for operation in (
        store.list_task_events("task-1"),
        store.append_event(event()),
        store.get_chain_head("task-1"),
    ):
        with pytest.raises(EventStoreError) as error:
            await operation
        assert error.value.code == "LOG_INVALID"
    assert path.read_bytes() == damage


async def test_failed_replace_retains_previous_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "events.jsonl"
    store = JsonlEventStore(path)
    await store.append_event(event())
    previous = path.read_bytes()

    def fail(*args):
        raise OSError("simulated replace failure")

    monkeypatch.setattr("ra_agent.events.store.os.replace", fail)
    with pytest.raises(EventStoreError) as error:
        await store.append_event(event(2))
    assert error.value.code == "STORAGE_UNAVAILABLE"
    assert path.read_bytes() == previous
    assert list(tmp_path.glob("*.tmp")) == []
    assert len(await store.list_task_events("task-1")) == 1


class FixtureChain:
    """Non-cryptographic test double for P3's structural HashChain interface."""

    def __init__(self, task_id: str) -> None:
        self.head = "0" * 64

    def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        previous = self.head
        self.head = f"{event['sequence']:064x}"
        return {
            "event": deepcopy(event),
            "prev_hash": previous,
            "event_digest": "f" * 64,
            "chain_hash": self.head,
        }

    def get_chain_head(self) -> str:
        return self.head


async def test_injected_chain_is_persisted_and_reconstructed(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    store = JsonlEventStore(path, chain_factory=FixtureChain)
    assert await store.get_chain_head("task-1") == "0" * 64
    await store.append_event(event())
    await store.append_event(event(2))
    restarted = JsonlEventStore(path, chain_factory=FixtureChain)
    assert await restarted.get_chain_head("task-1") == f"{2:064x}"
    assert "chain_hash" not in (await restarted.list_task_events("task-1"))[0]
    with pytest.raises(EventStoreError):
        await JsonlEventStore(path).list_task_events("task-1")
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["chain"]["chain_hash"] = "a" * 64
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(EventStoreError) as error:
        await restarted.list_task_events("task-1")
    assert error.value.code == "LOG_INVALID"


async def test_cannot_silently_add_chain_to_old_log(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    await JsonlEventStore(path).append_event(event())
    with pytest.raises(EventStoreError):
        await JsonlEventStore(path, chain_factory=FixtureChain).append_event(event(2))


def test_nullable_fields_are_required_and_extensions_roundtrip() -> None:
    with pytest.raises(ValidationError):
        BehaviorEvent.model_validate(
            {**event(), "sequence": 1, "parent_event_id": None, "actor": None}
        )
    missing = {**event(), "sequence": 1}
    del missing["result_digest"]
    with pytest.raises(ValidationError):
        BehaviorEvent.model_validate(missing)
    extended = {
        **event(),
        "sequence": 1,
        "prepared_effect_id": "effect-1",
        "effect_descriptor_digest": "a" * 64,
        "result_commitment": "opaque:fixture",
        "commit_epoch": 0,
        "execution_receipt_id": "receipt-1",
        "compliance_proof_ref": "fixture:proof",
    }
    output = BehaviorEvent.model_validate(extended).model_dump(mode="json")
    assert all(output[key] == value for key, value in extended.items())
