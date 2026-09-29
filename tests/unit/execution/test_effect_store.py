from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ra_agent.contracts import EffectRecord, EffectStatus
from ra_agent.execution.effect_store import (
    EffectConflictError,
    EffectIntegrityError,
    EffectStateTransitionError,
    FilesystemEffectStore,
)


def make_record(
    *,
    request_id: str = "request-1",
    effect_id: str = "11111111-1111-5111-8111-111111111111",
    task_id: str = "task-1",
    step_id: str = "step-1",
    kind: str = "FILE_WRITE",
    target_ref: str = "file:result.txt",
    checkpoint_id: str | None = "checkpoint-1",
    artifact_refs: list[str] | None = None,
    status: EffectStatus = EffectStatus.PENDING,
    created_at: datetime | None = None,
) -> EffectRecord:
    return EffectRecord(
        effect_id=effect_id,
        task_id=task_id,
        step_id=step_id,
        request_id=request_id,
        kind=kind,
        target_ref=target_ref,
        status=status,
        checkpoint_id=checkpoint_id,
        artifact_refs=artifact_refs
        or ["artifact:request-1:pending_file:" + "a" * 64 + ":4"],
        created_at=created_at or datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_register_and_query_effect_facts(tmp_path: Path) -> None:
    store = FilesystemEffectStore(tmp_path / "effects")
    record = make_record()

    registered = await store.register(record)

    assert await store.get(record.effect_id) == registered
    assert await store.get_by_request_id(record.request_id) == registered
    assert await store.list_by_task_id(record.task_id) == (registered,)
    assert await store.list_by_target(record.target_ref) == (registered,)
    assert await store.verify_integrity(record.request_id)


@pytest.mark.asyncio
async def test_same_request_and_semantics_are_idempotent(tmp_path: Path) -> None:
    store = FilesystemEffectStore(tmp_path / "effects")
    original = make_record(artifact_refs=["b", "a", "a"])
    registered = await store.register(original)
    retry = make_record(
        artifact_refs=["a", "b"],
        status=EffectStatus.COMMITTED,
        created_at=datetime.now(UTC) + timedelta(days=1),
    )

    repeated = await store.register(retry)

    assert repeated == registered
    assert repeated.status is EffectStatus.PENDING
    assert repeated.artifact_refs == ["a", "b"]
    assert repeated.created_at == registered.created_at


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("effect_id", "22222222-2222-5222-8222-222222222222"),
        ("task_id", "task-2"),
        ("step_id", "step-2"),
        ("kind", "FILE_DELETE"),
        ("target_ref", "file:other.txt"),
        ("checkpoint_id", "checkpoint-2"),
        ("artifact_refs", ["artifact:different"]),
    ],
)
async def test_request_reuse_with_different_semantics_conflicts(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    store = FilesystemEffectStore(tmp_path / "effects")
    record = make_record()
    await store.register(record)
    conflicting = record.model_copy(update={field: value})

    with pytest.raises(EffectConflictError):
        await store.register(conflicting)

    assert await store.get_by_request_id(record.request_id) == record


@pytest.mark.asyncio
async def test_effects_survive_store_restart(tmp_path: Path) -> None:
    root = tmp_path / "effects"
    record = make_record()
    await FilesystemEffectStore(root).register(record)

    reloaded = await FilesystemEffectStore(root).get_by_request_id(record.request_id)

    assert reloaded == record


@pytest.mark.asyncio
async def test_status_transitions_are_atomic_and_idempotent(tmp_path: Path) -> None:
    store = FilesystemEffectStore(tmp_path / "effects")
    record = await store.register(make_record())

    committed = await store.update_status(
        record.effect_id,
        expected_statuses=frozenset({EffectStatus.PENDING}),
        target_status=EffectStatus.COMMITTED,
    )
    repeated = await store.update_status(
        record.effect_id,
        expected_statuses=frozenset({EffectStatus.PENDING}),
        target_status=EffectStatus.COMMITTED,
    )
    rolled_back = await store.update_status(
        record.effect_id,
        expected_statuses=frozenset({EffectStatus.PENDING, EffectStatus.COMMITTED}),
        target_status=EffectStatus.ROLLED_BACK,
    )

    assert committed.status is EffectStatus.COMMITTED
    assert repeated == committed
    assert rolled_back.status is EffectStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_illegal_terminal_to_commit_transition_is_rejected(
    tmp_path: Path,
) -> None:
    store = FilesystemEffectStore(tmp_path / "effects")
    record = await store.register(make_record(status=EffectStatus.REJECTED))

    with pytest.raises(EffectStateTransitionError):
        await store.update_status(
            record.effect_id,
            expected_statuses=frozenset({EffectStatus.PENDING}),
            target_status=EffectStatus.COMMITTED,
        )


@pytest.mark.asyncio
async def test_manifest_tampering_fails_closed(tmp_path: Path) -> None:
    store = FilesystemEffectStore(tmp_path / "effects")
    record = await store.register(make_record())
    manifest = store.effect_root / record.request_id / store.MANIFEST_NAME
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["record"]["request_id"] = "other-request"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    assert not await store.verify_integrity(record.request_id)
    with pytest.raises(EffectIntegrityError):
        await store.get_by_request_id(record.request_id)


@pytest.mark.asyncio
async def test_unexpected_files_in_effect_directory_fail_closed(tmp_path: Path) -> None:
    store = FilesystemEffectStore(tmp_path / "effects")
    record = await store.register(make_record())
    (store.effect_root / record.request_id / "payload.bin").write_bytes(b"secret")

    with pytest.raises(EffectIntegrityError):
        await store.get_by_request_id(record.request_id)


@pytest.mark.asyncio
async def test_missing_effect_queries_are_explicit(tmp_path: Path) -> None:
    store = FilesystemEffectStore(tmp_path / "effects")

    assert await store.get_by_request_id("missing-request") is None
    assert await store.list_by_task_id("missing-task") == ()
    assert await store.list_by_target("file:missing.txt") == ()


@pytest.mark.asyncio
async def test_empty_expected_status_set_is_rejected(tmp_path: Path) -> None:
    store = FilesystemEffectStore(tmp_path / "effects")
    record = await store.register(make_record())

    with pytest.raises(ValueError):
        await store.update_status(
            record.effect_id,
            expected_statuses=frozenset(),
            target_status=EffectStatus.COMMITTED,
        )


@pytest.mark.asyncio
async def test_unsafe_root_entry_fails_closed(tmp_path: Path) -> None:
    store = FilesystemEffectStore(tmp_path / "effects")
    (store.effect_root / "unexpected.txt").write_text("not an effect", encoding="utf-8")

    with pytest.raises(EffectIntegrityError):
        await store.list_by_task_id("task-1")


def test_symbolic_link_effect_root_is_rejected(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    linked_root = tmp_path / "linked"
    try:
        linked_root.symlink_to(real_root, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable on this platform")

    with pytest.raises(Exception, match="symbolic link"):
        FilesystemEffectStore(linked_root)
