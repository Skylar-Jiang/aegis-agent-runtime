import asyncio
import json
from pathlib import Path

import pytest

from ra_agent.execution.pending_store import (
    PendingConflictError,
    PendingIntegrityError,
    PendingNotFoundError,
    PendingOperation,
    PendingStatus,
    PendingStore,
)


@pytest.fixture
def pending_root(tmp_path: Path) -> Path:
    root = tmp_path / "pending"
    root.mkdir()
    return root


@pytest.fixture
def store(
    pending_root: Path,
) -> PendingStore:
    return PendingStore(pending_root)


@pytest.mark.asyncio
async def test_stage_write_creates_manifest_and_payload(
    store: PendingStore,
    pending_root: Path,
) -> None:
    record = await store.stage_write(
        "request-001",
        "reports/result.md",
        b"hello",
    )

    assert record.request_id == "request-001"
    assert record.checkpoint_id is None
    assert record.tool_name == "write_file"
    assert record.operation is PendingOperation.WRITE
    assert record.target_path == "reports/result.md"
    assert record.size_bytes == 5
    assert record.status is PendingStatus.PENDING

    request_dir = pending_root / "request-001"
    assert (request_dir / "manifest.json").is_file()
    assert (request_dir / "payload.bin").read_bytes() == b"hello"


@pytest.mark.asyncio
async def test_stage_delete_creates_only_manifest(
    store: PendingStore,
    pending_root: Path,
) -> None:
    record = await store.stage_delete(
        "request-002",
        "old.txt",
    )

    assert record.operation is PendingOperation.DELETE
    assert record.tool_name == "delete_file"
    assert record.pending_path is None
    assert record.content_sha256 is None
    assert record.size_bytes is None

    request_dir = pending_root / "request-002"
    assert (request_dir / "manifest.json").is_file()
    assert not (request_dir / "payload.bin").exists()


@pytest.mark.asyncio
async def test_get_reloads_record(
    store: PendingStore,
) -> None:
    created = await store.stage_write(
        "request-003",
        "result.txt",
        b"content",
    )

    loaded = await store.get("request-003")

    assert loaded == created


@pytest.mark.asyncio
async def test_bind_checkpoint_updates_record(
    store: PendingStore,
) -> None:
    await store.stage_write(
        "request-004",
        "result.txt",
        b"content",
    )

    updated = await store.bind_checkpoint(
        "request-004",
        "checkpoint-004",
    )

    assert updated.checkpoint_id == "checkpoint-004"

    loaded = await store.get("request-004")
    assert loaded.checkpoint_id == "checkpoint-004"


@pytest.mark.asyncio
async def test_bind_same_checkpoint_is_idempotent(
    store: PendingStore,
) -> None:
    await store.stage_delete(
        "request-005",
        "old.txt",
    )

    first = await store.bind_checkpoint(
        "request-005",
        "checkpoint-005",
    )
    second = await store.bind_checkpoint(
        "request-005",
        "checkpoint-005",
    )

    assert first == second


@pytest.mark.asyncio
async def test_bind_different_checkpoint_is_rejected(
    store: PendingStore,
) -> None:
    await store.stage_delete(
        "request-006",
        "old.txt",
    )
    await store.bind_checkpoint(
        "request-006",
        "checkpoint-a",
    )

    with pytest.raises(PendingConflictError):
        await store.bind_checkpoint(
            "request-006",
            "checkpoint-b",
        )


@pytest.mark.asyncio
async def test_stage_write_is_idempotent(
    store: PendingStore,
) -> None:
    first = await store.stage_write(
        "request-007",
        "result.txt",
        b"same",
    )
    second = await store.stage_write(
        "request-007",
        "result.txt",
        b"same",
    )

    assert first == second


@pytest.mark.asyncio
async def test_reusing_request_id_with_different_content_is_rejected(
    store: PendingStore,
) -> None:
    await store.stage_write(
        "request-008",
        "result.txt",
        b"first",
    )

    with pytest.raises(PendingConflictError):
        await store.stage_write(
            "request-008",
            "result.txt",
            b"second",
        )


@pytest.mark.asyncio
async def test_reusing_request_id_with_different_operation_is_rejected(
    store: PendingStore,
) -> None:
    await store.stage_write(
        "request-009",
        "result.txt",
        b"content",
    )

    with pytest.raises(PendingConflictError):
        await store.stage_delete(
            "request-009",
            "result.txt",
        )


@pytest.mark.asyncio
async def test_verify_integrity_accepts_valid_write(
    store: PendingStore,
) -> None:
    await store.stage_write(
        "request-010",
        "result.txt",
        b"content",
    )

    assert await store.verify_integrity("request-010") is True


@pytest.mark.asyncio
async def test_verify_integrity_detects_payload_tampering(
    store: PendingStore,
    pending_root: Path,
) -> None:
    await store.stage_write(
        "request-011",
        "result.txt",
        b"original",
    )

    payload = pending_root / "request-011" / "payload.bin"
    payload.write_bytes(b"tampered")

    assert await store.verify_integrity("request-011") is False


@pytest.mark.asyncio
async def test_verify_integrity_detects_unexpected_file(
    store: PendingStore,
    pending_root: Path,
) -> None:
    await store.stage_delete(
        "request-012",
        "old.txt",
    )

    (pending_root / "request-012" / "unexpected.txt").write_text(
        "unexpected",
        encoding="utf-8",
    )

    assert await store.verify_integrity("request-012") is False


@pytest.mark.asyncio
async def test_mark_committed_requires_checkpoint(
    store: PendingStore,
) -> None:
    await store.stage_write(
        "request-013",
        "result.txt",
        b"content",
    )

    with pytest.raises(PendingConflictError):
        await store.mark_committed("request-013")


@pytest.mark.asyncio
async def test_mark_committed_persists_status(
    store: PendingStore,
) -> None:
    await store.stage_write(
        "request-014",
        "result.txt",
        b"content",
    )
    await store.bind_checkpoint(
        "request-014",
        "checkpoint-014",
    )

    await store.mark_committed("request-014")

    loaded = await store.get("request-014")
    assert loaded.status is PendingStatus.COMMITTED


@pytest.mark.asyncio
async def test_mark_committed_is_idempotent(
    store: PendingStore,
) -> None:
    await store.stage_delete(
        "request-015",
        "old.txt",
    )
    await store.bind_checkpoint(
        "request-015",
        "checkpoint-015",
    )

    await store.mark_committed("request-015")
    await store.mark_committed("request-015")

    loaded = await store.get("request-015")
    assert loaded.status is PendingStatus.COMMITTED


@pytest.mark.asyncio
async def test_cleanup_removes_request_directory(
    store: PendingStore,
    pending_root: Path,
) -> None:
    await store.stage_write(
        "request-016",
        "result.txt",
        b"content",
    )

    await store.cleanup("request-016")

    assert not (pending_root / "request-016").exists()


@pytest.mark.asyncio
async def test_cleanup_is_idempotent(
    store: PendingStore,
) -> None:
    await store.cleanup("request-017")
    await store.cleanup("request-017")


@pytest.mark.asyncio
async def test_get_missing_record_raises(
    store: PendingStore,
) -> None:
    with pytest.raises(PendingNotFoundError):
        await store.get("request-missing")


@pytest.mark.asyncio
async def test_corrupted_manifest_is_rejected(
    store: PendingStore,
    pending_root: Path,
) -> None:
    await store.stage_delete(
        "request-018",
        "old.txt",
    )

    manifest = pending_root / "request-018" / "manifest.json"
    manifest.write_text(
        "{invalid json",
        encoding="utf-8",
    )

    with pytest.raises(PendingIntegrityError):
        await store.get("request-018")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_id",
    [
        "../outside",
        "request/child",
        r"request\child",
        "",
        " ",
    ],
)
async def test_unsafe_request_ids_are_rejected(
    store: PendingStore,
    request_id: str,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        await store.stage_delete(
            request_id,
            "old.txt",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target_path",
    [
        "../outside.txt",
        "/etc/passwd",
        r"C:\secret.txt",
        r"folder\..\secret.txt",
        "",
        ".",
    ],
)
async def test_unsafe_target_paths_are_rejected(
    store: PendingStore,
    target_path: str,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        await store.stage_delete(
            "request-019",
            target_path,
        )


@pytest.mark.asyncio
async def test_stage_write_does_not_modify_workspace(
    store: PendingStore,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    target = workspace / "result.txt"
    target.write_text(
        "old",
        encoding="utf-8",
    )

    await store.stage_write(
        "request-020",
        "result.txt",
        b"new",
    )

    assert target.read_text(encoding="utf-8") == "old"


@pytest.mark.asyncio
async def test_concurrent_identical_stage_write_is_idempotent(
    store: PendingStore,
) -> None:
    first, second = await asyncio.gather(
        store.stage_write(
            "request-021",
            "result.txt",
            b"same",
        ),
        store.stage_write(
            "request-021",
            "result.txt",
            b"same",
        ),
    )

    assert first == second


@pytest.mark.asyncio
async def test_manifest_contains_expected_metadata(
    store: PendingStore,
    pending_root: Path,
) -> None:
    await store.stage_write(
        "request-022",
        "reports/result.md",
        b"hello",
    )

    manifest_path = pending_root / "request-022" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["version"] == 1
    assert manifest["request_id"] == "request-022"
    assert manifest["tool_name"] == "write_file"
    assert manifest["operation"] == "WRITE"
    assert manifest["target_path"] == "reports/result.md"
    assert manifest["status"] == "PENDING"


def test_pending_root_returns_resolved_path(
    store: PendingStore,
    pending_root: Path,
) -> None:
    assert store.pending_root == pending_root.resolve()


@pytest.mark.asyncio
async def test_stage_write_rejects_non_bytes_content(
    store: PendingStore,
) -> None:
    with pytest.raises(
        TypeError,
        match="content must be bytes",
    ):
        await store.stage_write(
            "request-023",
            "result.txt",
            "not bytes",  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_stage_delete_is_idempotent(
    store: PendingStore,
) -> None:
    first = await store.stage_delete(
        "request-024",
        "old.txt",
    )
    second = await store.stage_delete(
        "request-024",
        "old.txt",
    )

    assert first == second


@pytest.mark.asyncio
async def test_reusing_delete_request_id_with_different_target_is_rejected(
    store: PendingStore,
) -> None:
    await store.stage_delete(
        "request-025",
        "first.txt",
    )

    with pytest.raises(PendingConflictError):
        await store.stage_delete(
            "request-025",
            "second.txt",
        )


@pytest.mark.asyncio
async def test_bind_checkpoint_rejects_unsafe_identifier(
    store: PendingStore,
) -> None:
    await store.stage_delete(
        "request-026",
        "old.txt",
    )

    with pytest.raises(ValueError):
        await store.bind_checkpoint(
            "request-026",
            "../checkpoint",
        )


@pytest.mark.asyncio
async def test_mark_committed_rejects_tampered_payload(
    store: PendingStore,
    pending_root: Path,
) -> None:
    await store.stage_write(
        "request-027",
        "result.txt",
        b"original",
    )
    await store.bind_checkpoint(
        "request-027",
        "checkpoint-027",
    )

    payload_path = pending_root / "request-027" / "payload.bin"
    payload_path.write_bytes(b"tampered")

    with pytest.raises(PendingIntegrityError):
        await store.mark_committed("request-027")


@pytest.mark.asyncio
async def test_verify_integrity_detects_missing_payload(
    store: PendingStore,
    pending_root: Path,
) -> None:
    await store.stage_write(
        "request-028",
        "result.txt",
        b"content",
    )

    (pending_root / "request-028" / "payload.bin").unlink()

    assert await store.verify_integrity("request-028") is False


@pytest.mark.asyncio
async def test_get_rejects_unsupported_manifest_version(
    store: PendingStore,
    pending_root: Path,
) -> None:
    await store.stage_delete(
        "request-029",
        "old.txt",
    )

    manifest_path = pending_root / "request-029" / "manifest.json"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = 999

    manifest_path.write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(
        PendingIntegrityError,
        match="unsupported",
    ):
        await store.get("request-029")


@pytest.mark.asyncio
async def test_committed_record_cannot_be_rebound(
    store: PendingStore,
) -> None:
    await store.stage_delete(
        "request-030",
        "old.txt",
    )
    await store.bind_checkpoint(
        "request-030",
        "checkpoint-030",
    )
    await store.mark_committed("request-030")

    with pytest.raises(PendingConflictError):
        await store.bind_checkpoint(
            "request-030",
            "checkpoint-030",
        )
