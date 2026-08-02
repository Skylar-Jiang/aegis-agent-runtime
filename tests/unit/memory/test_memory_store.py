from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ra_agent.contracts import MemoryStatus, SourceType, ToolCallRequest
from ra_agent.memory import (
    FilesystemMemoryStore,
    MemoryConflictError,
    MemoryIntegrityError,
    MemoryStateTransitionError,
    MemoryValueError,
)


def make_request(
    request_id: str,
    *,
    task_id: str = "task-1",
    step_id: str = "step-1",
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id=task_id,
        step_id=step_id,
        request_id=request_id,
        tool_name="memory_write",
        arguments={},
        objective="persist runtime memory safely",
        context_summary="memory store unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


@pytest.fixture
def memory_root(tmp_path: Path) -> Path:
    return tmp_path / "memory"


@pytest.fixture
def store(memory_root: Path) -> FilesystemMemoryStore:
    return FilesystemMemoryStore(memory_root, max_value_bytes=4096)


@pytest.mark.asyncio
async def test_stage_persists_pending_canonical_json(
    store: FilesystemMemoryStore,
    memory_root: Path,
) -> None:
    request = make_request("request-stage")

    record = await store.stage(
        request,
        key="user.preference",
        value={"b": 2, "a": 1},
    )

    assert record.status is MemoryStatus.PENDING
    assert record.memory_id == "memory-request-stage"
    assert record.key == "user.preference"
    assert record.task_id == request.task_id
    assert record.step_id == request.step_id
    assert record.request_id == request.request_id
    assert record.payload_path == "records/request-stage/payload.json"
    assert await store.verify_integrity(request.request_id) is True
    assert await store.read_value(request.request_id) == {"a": 1, "b": 2}
    assert (memory_root / record.payload_path).read_bytes() == b'{"a":1,"b":2}'


@pytest.mark.asyncio
async def test_same_request_and_semantics_are_idempotent(
    store: FilesystemMemoryStore,
) -> None:
    request = make_request("request-idempotent")

    first = await store.stage(request, key="profile.name", value="Elaine")
    second = await store.stage(request, key="profile.name", value="Elaine")

    assert first == second
    assert len(list(store.records_root.iterdir())) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "value", "task_id", "step_id"),
    [
        ("profile.other", "Elaine", "task-1", "step-1"),
        ("profile.name", "Theseus", "task-1", "step-1"),
        ("profile.name", "Elaine", "task-other", "step-1"),
        ("profile.name", "Elaine", "task-1", "step-other"),
    ],
)
async def test_reused_request_with_different_semantics_conflicts(
    store: FilesystemMemoryStore,
    key: str,
    value: object,
    task_id: str,
    step_id: str,
) -> None:
    await store.stage(
        make_request("request-conflict"),
        key="profile.name",
        value="Elaine",
    )

    with pytest.raises(MemoryConflictError):
        await store.stage(
            make_request(
                "request-conflict",
                task_id=task_id,
                step_id=step_id,
            ),
            key=key,
            value=value,
        )


@pytest.mark.asyncio
async def test_pending_memory_is_not_trusted_visible(
    store: FilesystemMemoryStore,
) -> None:
    await store.stage(
        make_request("request-pending-hidden"),
        key="agent.note",
        value={"text": "untrusted"},
    )

    assert await store.get_trusted("agent.note") is None
    assert await store.get_trusted_value("agent.note") is None
    assert [record.request_id for record in await store.list_pending()] == [
        "request-pending-hidden"
    ]


@pytest.mark.asyncio
async def test_trusted_memory_is_visible_after_transition(
    store: FilesystemMemoryStore,
) -> None:
    request = make_request("request-trusted")
    await store.stage(request, key="agent.note", value={"text": "safe"})

    trusted = await store.mark_trusted(request.request_id)
    visible = await store.get_trusted_value("agent.note")

    assert trusted.status is MemoryStatus.TRUSTED
    assert trusted.trusted_at is not None
    assert visible is not None
    visible_record, value = visible
    assert visible_record.request_id == request.request_id
    assert value == {"text": "safe"}
    assert await store.list_pending() == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [MemoryStatus.REJECTED, MemoryStatus.ROLLED_BACK])
async def test_rejected_and_rolled_back_memory_are_not_visible(
    store: FilesystemMemoryStore,
    terminal: MemoryStatus,
) -> None:
    request = make_request(f"request-{terminal.value.lower()}")
    await store.stage(request, key="agent.note", value="unsafe")

    if terminal is MemoryStatus.REJECTED:
        record = await store.mark_rejected(
            request.request_id, reason="post check rejected"
        )
    else:
        record = await store.mark_rolled_back(
            request.request_id, reason="runtime cancelled"
        )

    assert record.status is terminal
    assert await store.get_trusted("agent.note") is None


@pytest.mark.asyncio
async def test_trusted_version_can_be_rolled_back_to_previous_trusted_version(
    store: FilesystemMemoryStore,
) -> None:
    first = make_request("request-version-a", step_id="step-a")
    second = make_request("request-version-b", step_id="step-b")

    await store.stage(first, key="project.mode", value="safe-a")
    await store.mark_trusted(first.request_id)
    await store.stage(second, key="project.mode", value="poisoned-b")
    await store.mark_trusted(second.request_id)

    visible_before = await store.get_trusted_value("project.mode")
    assert visible_before is not None
    assert visible_before[0].request_id == second.request_id
    assert visible_before[1] == "poisoned-b"

    rolled_back = await store.mark_rolled_back(
        second.request_id,
        reason="post-commit poisoning discovered",
    )
    visible_after = await store.get_trusted_value("project.mode")

    assert rolled_back.status is MemoryStatus.ROLLED_BACK
    assert visible_after is not None
    assert visible_after[0].request_id == first.request_id
    assert visible_after[1] == "safe-a"


@pytest.mark.asyncio
async def test_terminal_transitions_are_idempotent(
    store: FilesystemMemoryStore,
) -> None:
    trusted_request = make_request("request-idempotent-trusted")
    await store.stage(trusted_request, key="trusted", value=1)
    first_trusted = await store.mark_trusted(trusted_request.request_id)
    second_trusted = await store.mark_trusted(trusted_request.request_id)
    assert first_trusted == second_trusted

    rejected_request = make_request("request-idempotent-rejected")
    await store.stage(rejected_request, key="rejected", value=2)
    first_rejected = await store.mark_rejected(
        rejected_request.request_id,
        reason="unsafe",
    )
    second_rejected = await store.mark_rejected(
        rejected_request.request_id,
        reason="different retry reason",
    )
    assert first_rejected == second_rejected

    rolled_request = make_request("request-idempotent-rollback")
    await store.stage(rolled_request, key="rolled", value=3)
    first_rolled = await store.mark_rolled_back(
        rolled_request.request_id,
        reason="cancelled",
    )
    second_rolled = await store.mark_rolled_back(
        rolled_request.request_id,
        reason="retry",
    )
    assert first_rolled == second_rolled


@pytest.mark.asyncio
async def test_invalid_reverse_transitions_are_rejected(
    store: FilesystemMemoryStore,
) -> None:
    rejected_request = make_request("request-no-trust-after-reject")
    await store.stage(rejected_request, key="key-a", value=1)
    await store.mark_rejected(rejected_request.request_id, reason="unsafe")

    with pytest.raises(MemoryStateTransitionError):
        await store.mark_trusted(rejected_request.request_id)

    rolled_request = make_request("request-no-trust-after-rollback")
    await store.stage(rolled_request, key="key-b", value=2)
    await store.mark_rolled_back(rolled_request.request_id, reason="cancelled")

    with pytest.raises(MemoryStateTransitionError):
        await store.mark_trusted(rolled_request.request_id)


@pytest.mark.asyncio
async def test_payload_tampering_fails_closed(
    store: FilesystemMemoryStore,
    memory_root: Path,
) -> None:
    request = make_request("request-payload-tamper")
    record = await store.stage(request, key="key", value={"safe": True})
    (memory_root / record.payload_path).write_bytes(b'{"safe":false}')

    assert await store.verify_integrity(request.request_id) is False
    with pytest.raises(MemoryIntegrityError):
        await store.mark_trusted(request.request_id)


@pytest.mark.asyncio
async def test_manifest_tampering_fails_closed(
    store: FilesystemMemoryStore,
) -> None:
    request = make_request("request-manifest-tamper")
    await store.stage(request, key="key", value={"safe": True})
    manifest_path = store.records_root / request.request_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["payload_path"] = "../outside.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert await store.verify_integrity(request.request_id) is False


@pytest.mark.asyncio
async def test_store_state_survives_restart(memory_root: Path) -> None:
    request = make_request("request-restart")
    first_store = FilesystemMemoryStore(memory_root, max_value_bytes=4096)
    await first_store.stage(request, key="restart.key", value={"value": 7})
    await first_store.mark_trusted(request.request_id)

    restarted = FilesystemMemoryStore(memory_root, max_value_bytes=4096)
    visible = await restarted.get_trusted_value("restart.key")

    assert visible is not None
    assert visible[0].status is MemoryStatus.TRUSTED
    assert visible[1] == {"value": 7}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [
        {"not-json": {1, 2}},
        float("nan"),
        float("inf"),
    ],
)
async def test_non_finite_or_non_json_values_are_rejected(
    store: FilesystemMemoryStore,
    value: object,
) -> None:
    with pytest.raises(MemoryValueError):
        await store.stage(make_request("request-invalid-value"), key="key", value=value)


@pytest.mark.asyncio
async def test_value_size_limit_is_enforced(tmp_path: Path) -> None:
    store = FilesystemMemoryStore(tmp_path / "memory", max_value_bytes=8)

    with pytest.raises(MemoryValueError, match="exceeds configured limit"):
        await store.stage(
            make_request("request-too-large"),
            key="key",
            value="this value is too large",
        )
