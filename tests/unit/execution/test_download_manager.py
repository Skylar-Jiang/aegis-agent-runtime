from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from ra_agent.contracts import (
    ExecutionStatus,
    PostCheckResult,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution.artifacts import (
    build_quarantined_download_artifact,
    build_tool_output_artifact,
)
from ra_agent.execution.download_manager import (
    DownloadLifecycleCorrelationError,
    DownloadLifecycleIntegrityError,
    DownloadLifecycleManager,
    DownloadLifecyclePreconditionError,
)
from ra_agent.execution.quarantine import (
    FilesystemQuarantineStore,
    QuarantineRecord,
    QuarantineStatus,
)


def make_request(request_id: str) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id=request_id,
        tool_name="download_url",
        arguments={"url": "https://example.com/file"},
        objective="inspect a quarantined download",
        context_summary="download lifecycle unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


@pytest.fixture
def store(tmp_path: Path) -> FilesystemQuarantineStore:
    return FilesystemQuarantineStore(tmp_path / "quarantine", max_download_bytes=4096)


async def stage(
    store: FilesystemQuarantineStore,
    request: ToolCallRequest,
) -> QuarantineRecord:
    payload = b"safe content"
    temporary = store.create_temporary_path(request.request_id)
    temporary.write_bytes(payload)
    return await store.stage(
        request,
        source_url="https://example.com/file",
        final_url="https://example.com/file",
        redirect_chain=(),
        temporary_path=temporary,
        content_type="text/plain",
        content_sha256=sha256(payload).hexdigest(),
        size_bytes=len(payload),
        http_status=200,
    )


def make_execution(
    request: ToolCallRequest,
    record: QuarantineRecord,
) -> ToolExecutionResult:
    output = {
        "downloaded": True,
        "source_url": record.source_url,
        "final_url": record.final_url,
        "content_type": record.content_type,
        "size_bytes": record.size_bytes,
        "sha256": record.content_sha256,
        "status": record.status.value,
    }
    return ToolExecutionResult(
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        status=ExecutionStatus.PENDING_COMMIT,
        output=output,
        artifacts=[
            build_tool_output_artifact(
                request,
                output,
                status=ExecutionStatus.PENDING_COMMIT,
            ),
            build_quarantined_download_artifact(
                request,
                quarantine_path=record.quarantine_path,
                source_url=record.source_url,
                final_url=record.final_url,
                content_sha256=record.content_sha256,
                size_bytes=record.size_bytes,
                content_type=record.content_type,
            ),
        ],
        pending_changes=[
            {
                "operation": "DOWNLOAD",
                "quarantine_path": record.quarantine_path,
                "source_url": record.source_url,
                "final_url": record.final_url,
                "content_sha256": record.content_sha256,
                "size_bytes": record.size_bytes,
                "status": record.status.value,
            }
        ],
    )


@pytest.mark.asyncio
async def test_passing_post_check_commits_approved_quarantine(
    store: FilesystemQuarantineStore,
) -> None:
    request = make_request("request-commit")
    record = await stage(store, request)
    execution = make_execution(request, record)

    result = await DownloadLifecycleManager(store).commit(
        request,
        execution,
        PostCheckResult(request_id=request.request_id, passed=True, reason="safe"),
    )

    assert result.status is QuarantineStatus.COMMITTED
    assert await store.verify_integrity(request.request_id)


@pytest.mark.asyncio
async def test_failing_post_check_rejects_quarantine(
    store: FilesystemQuarantineStore,
) -> None:
    request = make_request("request-reject")
    record = await stage(store, request)
    execution = make_execution(request, record)

    result = await DownloadLifecycleManager(store).reject(
        request,
        execution,
        PostCheckResult(
            request_id=request.request_id, passed=False, reason="injection"
        ),
    )

    assert result.status is QuarantineStatus.REJECTED
    assert result.rejected_reason == "injection"


@pytest.mark.asyncio
async def test_commit_requires_passing_post_check(
    store: FilesystemQuarantineStore,
) -> None:
    request = make_request("request-precondition")
    record = await stage(store, request)

    with pytest.raises(DownloadLifecyclePreconditionError):
        await DownloadLifecycleManager(store).commit(
            request,
            make_execution(request, record),
            PostCheckResult(
                request_id=request.request_id, passed=False, reason="unsafe"
            ),
        )


@pytest.mark.asyncio
async def test_lifecycle_rejects_request_id_mismatch(
    store: FilesystemQuarantineStore,
) -> None:
    request = make_request("request-correlation")
    record = await stage(store, request)

    with pytest.raises(DownloadLifecycleCorrelationError):
        await DownloadLifecycleManager(store).commit(
            request,
            make_execution(request, record),
            PostCheckResult(request_id="request-other", passed=True, reason="safe"),
        )


@pytest.mark.asyncio
async def test_lifecycle_rejects_artifact_digest_mismatch(
    store: FilesystemQuarantineStore,
) -> None:
    request = make_request("request-artifact-mismatch")
    record = await stage(store, request)
    execution = make_execution(request, record)
    execution.artifacts[1]["sha256"] = sha256(b"different").hexdigest()

    with pytest.raises(DownloadLifecycleIntegrityError):
        await DownloadLifecycleManager(store).commit(
            request,
            execution,
            PostCheckResult(request_id=request.request_id, passed=True, reason="safe"),
        )


@pytest.mark.asyncio
async def test_payload_tamper_prevents_commit(
    store: FilesystemQuarantineStore,
) -> None:
    request = make_request("request-payload-tamper")
    record = await stage(store, request)
    execution = make_execution(request, record)
    (await store.payload_path(request.request_id)).write_bytes(b"tampered")

    with pytest.raises(DownloadLifecycleIntegrityError):
        await DownloadLifecycleManager(store).commit(
            request,
            execution,
            PostCheckResult(request_id=request.request_id, passed=True, reason="safe"),
        )


@pytest.mark.asyncio
async def test_rollback_is_idempotent(store: FilesystemQuarantineStore) -> None:
    request = make_request("request-rollback")
    await stage(store, request)
    manager = DownloadLifecycleManager(store)

    first = await manager.rollback(request.request_id, reason="cancelled")
    second = await manager.rollback(request.request_id, reason="again")

    assert first.status is QuarantineStatus.ROLLED_BACK
    assert second.status is QuarantineStatus.ROLLED_BACK
