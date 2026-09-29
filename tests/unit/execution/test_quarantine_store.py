from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from ra_agent.contracts import SourceType, ToolCallRequest
from ra_agent.execution.quarantine import (
    FilesystemQuarantineStore,
    QuarantineConflictError,
    QuarantineIntegrityError,
    QuarantineRecord,
    QuarantineStateTransitionError,
    QuarantineStatus,
)


def make_request(request_id: str, *, task_id: str = "task-1") -> ToolCallRequest:
    return ToolCallRequest(
        task_id=task_id,
        step_id="step-1",
        request_id=request_id,
        tool_name="download_url",
        arguments={"url": "https://example.com/file"},
        objective="download into quarantine",
        context_summary="quarantine store unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


@pytest.fixture
def store(tmp_path: Path) -> FilesystemQuarantineStore:
    return FilesystemQuarantineStore(tmp_path / "quarantine", max_download_bytes=1024)


def make_temp(
    store: FilesystemQuarantineStore, request_id: str, payload: bytes
) -> Path:
    path = store.create_temporary_path(request_id)
    path.write_bytes(payload)
    return path


async def stage(
    store: FilesystemQuarantineStore,
    request: ToolCallRequest,
    *,
    payload: bytes = b"downloaded content",
    source_url: str = "https://example.com/file",
    final_url: str = "https://cdn.example.com/file",
) -> QuarantineRecord:
    return await store.stage(
        request,
        source_url=source_url,
        final_url=final_url,
        redirect_chain=(final_url,),
        temporary_path=make_temp(store, request.request_id, payload),
        content_type="application/octet-stream",
        content_sha256=sha256(payload).hexdigest(),
        size_bytes=len(payload),
        http_status=200,
    )


@pytest.mark.asyncio
async def test_stage_persists_quarantined_payload_and_manifest(
    store: FilesystemQuarantineStore,
) -> None:
    request = make_request("request-stage")
    record = await stage(store, request)

    assert record.status is QuarantineStatus.QUARANTINED
    assert record.quarantine_path == "request-stage/payload.bin"
    assert await store.verify_integrity(request.request_id)
    assert (
        await store.payload_path(request.request_id)
    ).read_bytes() == b"downloaded content"


@pytest.mark.asyncio
async def test_stage_is_idempotent_for_identical_semantics(
    store: FilesystemQuarantineStore,
) -> None:
    request = make_request("request-idempotent")
    first = await stage(store, request)
    second = await stage(store, request)

    assert second == first


@pytest.mark.asyncio
async def test_stage_rejects_request_id_reuse_with_different_url(
    store: FilesystemQuarantineStore,
) -> None:
    request = make_request("request-conflict")
    await stage(store, request)

    with pytest.raises(QuarantineConflictError):
        await stage(
            store,
            request,
            source_url="https://other.example/file",
        )


@pytest.mark.asyncio
async def test_payload_tampering_fails_integrity(
    store: FilesystemQuarantineStore,
) -> None:
    request = make_request("request-payload-tamper")
    await stage(store, request)
    (store.quarantine_root / request.request_id / "payload.bin").write_bytes(
        b"tampered"
    )

    assert not await store.verify_integrity(request.request_id)


@pytest.mark.asyncio
async def test_manifest_path_traversal_fails_integrity(
    store: FilesystemQuarantineStore,
) -> None:
    request = make_request("request-manifest-tamper")
    await stage(store, request)
    manifest_path = store.quarantine_root / request.request_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["quarantine_path"] = "../payload.bin"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert not await store.verify_integrity(request.request_id)


@pytest.mark.asyncio
async def test_restart_recovers_quarantine_record(tmp_path: Path) -> None:
    root = tmp_path / "quarantine"
    first_store = FilesystemQuarantineStore(root, max_download_bytes=1024)
    request = make_request("request-restart")
    original = await stage(first_store, request)

    restarted = FilesystemQuarantineStore(root, max_download_bytes=1024)
    assert await restarted.get(request.request_id) == original
    assert await restarted.verify_integrity(request.request_id)


@pytest.mark.asyncio
async def test_lifecycle_transitions_are_idempotent_and_fail_closed(
    store: FilesystemQuarantineStore,
) -> None:
    request = make_request("request-lifecycle")
    await stage(store, request)

    committed = await store.mark_committed(request.request_id)
    assert committed.status is QuarantineStatus.COMMITTED
    assert await store.mark_committed(request.request_id) == committed

    rolled_back = await store.mark_rolled_back(
        request.request_id, reason="undo approval"
    )
    assert rolled_back.status is QuarantineStatus.ROLLED_BACK
    assert (
        await store.mark_rolled_back(request.request_id, reason="again")
    ).status is (QuarantineStatus.ROLLED_BACK)

    with pytest.raises(QuarantineStateTransitionError):
        await store.mark_committed(request.request_id)


@pytest.mark.asyncio
async def test_rejected_quarantine_cannot_be_committed(
    store: FilesystemQuarantineStore,
) -> None:
    request = make_request("request-rejected")
    await stage(store, request)
    rejected = await store.mark_rejected(request.request_id, reason="unsafe")

    assert rejected.status is QuarantineStatus.REJECTED
    with pytest.raises(QuarantineStateTransitionError):
        await store.mark_committed(request.request_id)


@pytest.mark.asyncio
async def test_stage_rejects_hash_mismatch(store: FilesystemQuarantineStore) -> None:
    request = make_request("request-bad-hash")
    payload = b"payload"
    with pytest.raises(QuarantineIntegrityError):
        await store.stage(
            request,
            source_url="https://example.com/file",
            final_url="https://example.com/file",
            redirect_chain=(),
            temporary_path=make_temp(store, request.request_id, payload),
            content_type="application/octet-stream",
            content_sha256=sha256(b"different").hexdigest(),
            size_bytes=len(payload),
            http_status=200,
        )
