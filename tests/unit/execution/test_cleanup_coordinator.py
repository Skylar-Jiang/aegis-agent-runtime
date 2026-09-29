from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from ra_agent.contracts import (
    DeepCheckResult,
    ExecutionStatus,
    MemoryStatus,
    PostCheckResult,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution.artifacts import (
    build_quarantined_download_artifact,
    build_tool_output_artifact,
)
from ra_agent.execution.checkpoint import CheckpointStatus, FilesystemCheckpointManager
from ra_agent.execution.cleanup import (
    CleanupContext,
    CleanupIncompleteError,
    RequestCleanupCoordinator,
)
from ra_agent.execution.commit_gate import FilesystemCommitGate
from ra_agent.execution.download_manager import DownloadLifecycleManager
from ra_agent.execution.pending_store import PendingStore
from ra_agent.execution.quarantine import (
    FilesystemQuarantineStore,
    QuarantineRecord,
    QuarantineStatus,
)
from ra_agent.execution.rollback import FilesystemRollbackManager
from ra_agent.memory import FilesystemMemoryStore, MemoryLifecycleManager
from ra_agent.tools.implementations.memory_tools import MemoryWriteHandler
from ra_agent.tools.implementations.write_file import WriteFileHandler
from ra_agent.tools.path_resolver import SafePathResolver


def make_request(
    tool_name: str,
    request_id: str,
    arguments: dict[str, object],
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id=request_id,
        tool_name=tool_name,
        arguments=arguments,
        objective="verify request-scoped cleanup",
        context_summary="cleanup coordinator unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def failing_post_check(request_id: str) -> PostCheckResult:
    return PostCheckResult(
        request_id=request_id,
        passed=False,
        reason="post check rejected pending content",
        signals=["UNSAFE_CONTENT"],
    )


def passed_deep_check(request_id: str) -> DeepCheckResult:
    return DeepCheckResult(request_id=request_id, passed=True, reason="safe")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "result.txt").write_text("original", encoding="utf-8")
    return root


@pytest.fixture
def resolver(workspace: Path) -> SafePathResolver:
    return SafePathResolver(
        workspace,
        max_path_length=4096,
        max_read_bytes=1024 * 1024,
        max_write_bytes=1024 * 1024,
    )


@pytest.fixture
def pending_store(tmp_path: Path) -> PendingStore:
    return PendingStore(tmp_path / "pending")


@pytest.fixture
def checkpoint_manager(
    tmp_path: Path,
    resolver: SafePathResolver,
) -> FilesystemCheckpointManager:
    return FilesystemCheckpointManager(tmp_path / "checkpoints", resolver)


@pytest.fixture
def rollback_manager(
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
) -> FilesystemRollbackManager:
    return FilesystemRollbackManager(resolver, pending_store, checkpoint_manager)


@pytest.fixture
def commit_gate(
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
) -> FilesystemCommitGate:
    return FilesystemCommitGate(resolver, pending_store, checkpoint_manager)


@pytest.fixture
def memory_store(tmp_path: Path) -> FilesystemMemoryStore:
    return FilesystemMemoryStore(tmp_path / "memory", max_value_bytes=4096)


@pytest.fixture
def memory_manager(memory_store: FilesystemMemoryStore) -> MemoryLifecycleManager:
    return MemoryLifecycleManager(memory_store)


@pytest.fixture
def quarantine_store(tmp_path: Path) -> FilesystemQuarantineStore:
    return FilesystemQuarantineStore(tmp_path / "quarantine", max_download_bytes=4096)


@pytest.fixture
def download_manager(
    quarantine_store: FilesystemQuarantineStore,
) -> DownloadLifecycleManager:
    return DownloadLifecycleManager(quarantine_store)


@pytest.fixture
def coordinator(
    pending_store: PendingStore,
    rollback_manager: FilesystemRollbackManager,
    memory_manager: MemoryLifecycleManager,
    download_manager: DownloadLifecycleManager,
) -> RequestCleanupCoordinator:
    return RequestCleanupCoordinator(
        pending_store=pending_store,
        rollback_manager=rollback_manager,
        memory_manager=memory_manager,
        download_manager=download_manager,
    )


async def prepare_file_execution(
    request: ToolCallRequest,
    *,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
) -> ToolExecutionResult:
    checkpoint = await checkpoint_manager.create(request)
    execution = await WriteFileHandler(resolver, pending_store)(request)
    await pending_store.bind_checkpoint(request.request_id, checkpoint.checkpoint_id)
    return execution.model_copy(update={"checkpoint_id": checkpoint.checkpoint_id})


async def stage_download(
    store: FilesystemQuarantineStore,
    request: ToolCallRequest,
) -> QuarantineRecord:
    payload = b"downloaded content"
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


def download_execution(
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
async def test_abort_rolls_back_pending_memory_and_is_idempotent(
    coordinator: RequestCleanupCoordinator,
    memory_store: FilesystemMemoryStore,
) -> None:
    request = make_request(
        "memory_write",
        "request-memory-abort",
        {"key": "project.note", "value": {"text": "candidate"}},
    )
    await MemoryWriteHandler(memory_store)(request)
    context = CleanupContext(
        request.task_id,
        request.step_id,
        request.request_id,
        request.tool_name,
    )

    first = await coordinator.abort(context, reason="runtime cancelled")
    second = await coordinator.abort(context, reason="retry cleanup")

    assert first.succeeded and second.succeeded
    assert (
        await memory_store.get(request.request_id)
    ).status is MemoryStatus.ROLLED_BACK
    assert await memory_store.get_trusted("project.note") is None


@pytest.mark.asyncio
async def test_reject_marks_pending_memory_rejected(
    coordinator: RequestCleanupCoordinator,
    memory_store: FilesystemMemoryStore,
) -> None:
    request = make_request(
        "memory_write",
        "request-memory-reject",
        {"key": "project.note", "value": "unsafe"},
    )
    execution = await MemoryWriteHandler(memory_store)(request)

    report = await coordinator.reject(
        request,
        execution,
        failing_post_check(request.request_id),
    )

    assert report.succeeded
    assert (await memory_store.get(request.request_id)).status is MemoryStatus.REJECTED
    assert await memory_store.get_trusted("project.note") is None


@pytest.mark.asyncio
async def test_abort_rolls_back_quarantine_and_repeated_cleanup_is_safe(
    coordinator: RequestCleanupCoordinator,
    quarantine_store: FilesystemQuarantineStore,
) -> None:
    request = make_request(
        "download_url",
        "request-download-abort",
        {"url": "https://example.com/file"},
    )
    await stage_download(quarantine_store, request)
    context = CleanupContext(
        request.task_id,
        request.step_id,
        request.request_id,
        request.tool_name,
    )

    await coordinator.abort(context, reason="network task cancelled")
    await coordinator.abort(context, reason="retry cleanup")

    assert (
        await quarantine_store.get(request.request_id)
    ).status is QuarantineStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_reject_marks_quarantine_rejected(
    coordinator: RequestCleanupCoordinator,
    quarantine_store: FilesystemQuarantineStore,
) -> None:
    request = make_request(
        "download_url",
        "request-download-reject",
        {"url": "https://example.com/file"},
    )
    record = await stage_download(quarantine_store, request)

    report = await coordinator.reject(
        request,
        download_execution(request, record),
        failing_post_check(request.request_id),
    )

    assert report.succeeded
    assert (
        await quarantine_store.get(request.request_id)
    ).status is QuarantineStatus.REJECTED


@pytest.mark.asyncio
async def test_abort_rolls_back_pending_file_without_changing_workspace(
    coordinator: RequestCleanupCoordinator,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    request = make_request(
        "write_file",
        "request-file-abort",
        {"path": "result.txt", "content": "pending"},
    )
    execution = await prepare_file_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )

    report = await coordinator.abort(
        CleanupContext(
            request.task_id,
            request.step_id,
            request.request_id,
            request.tool_name,
            execution.checkpoint_id,
        ),
        reason="post check failed",
    )

    assert report.succeeded
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "original"
    assert not (pending_store.pending_root / request.request_id).exists()
    assert (
        await checkpoint_manager.get(execution.checkpoint_id or "")
    ).status is CheckpointStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_failing_post_check_rolls_back_pending_file(
    coordinator: RequestCleanupCoordinator,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    request = make_request(
        "write_file",
        "request-file-reject",
        {"path": "result.txt", "content": "unsafe"},
    )
    execution = await prepare_file_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )

    report = await coordinator.reject(
        request,
        execution,
        failing_post_check(request.request_id),
    )

    assert report.succeeded
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "original"
    assert not (pending_store.pending_root / request.request_id).exists()


@pytest.mark.asyncio
async def test_failed_commit_cleanup_restores_original_file(
    coordinator: RequestCleanupCoordinator,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
) -> None:
    request = make_request(
        "write_file",
        "request-commit-rollback",
        {"path": "result.txt", "content": "committed"},
    )
    execution = await prepare_file_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    await commit_gate.commit(execution, passed_deep_check(request.request_id))
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "committed"

    await coordinator.rollback_failed_commit(
        CleanupContext(
            request.task_id,
            request.step_id,
            request.request_id,
            request.tool_name,
            execution.checkpoint_id,
        ),
        reason="commit finalization raised OSError",
    )

    assert (workspace / "result.txt").read_text(encoding="utf-8") == "original"


@pytest.mark.asyncio
async def test_failed_commit_cleanup_preserves_later_user_change(
    coordinator: RequestCleanupCoordinator,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
) -> None:
    request = make_request(
        "write_file",
        "request-conflict-preserved",
        {"path": "result.txt", "content": "committed"},
    )
    execution = await prepare_file_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    await commit_gate.commit(execution, passed_deep_check(request.request_id))
    (workspace / "result.txt").write_text("user changed later", encoding="utf-8")

    with pytest.raises(CleanupIncompleteError):
        await coordinator.rollback_failed_commit(
            CleanupContext(
                request.task_id,
                request.step_id,
                request.request_id,
                request.tool_name,
                execution.checkpoint_id,
            ),
            reason="late commit failure",
        )

    assert (workspace / "result.txt").read_text(
        encoding="utf-8"
    ) == "user changed later"
    assert (pending_store.pending_root / request.request_id).exists()


@pytest.mark.asyncio
async def test_cleanup_failure_can_be_retried(
    coordinator: RequestCleanupCoordinator,
    memory_store: FilesystemMemoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = make_request(
        "memory_write",
        "request-retry-cleanup",
        {"key": "project.note", "value": "candidate"},
    )
    await MemoryWriteHandler(memory_store)(request)
    original_cleanup = memory_store.cleanup
    calls = 0

    async def flaky_cleanup(request_id: str) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temporary cleanup failure")
        await original_cleanup(request_id)

    monkeypatch.setattr(memory_store, "cleanup", flaky_cleanup)
    context = CleanupContext(
        request.task_id,
        request.step_id,
        request.request_id,
        request.tool_name,
    )

    with pytest.raises(CleanupIncompleteError) as captured:
        await coordinator.abort(context, reason="cancelled")
    assert "memory_temp_cleanup" in str(captured.value)

    report = await coordinator.abort(context, reason="cleanup retry")
    assert report.succeeded
    assert calls == 2
    assert (
        await memory_store.get(request.request_id)
    ).status is MemoryStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_cleanup_isolated_by_request_id(
    coordinator: RequestCleanupCoordinator,
    memory_store: FilesystemMemoryStore,
) -> None:
    first = make_request(
        "memory_write",
        "request-isolation-a",
        {"key": "project.a", "value": "A"},
    )
    second = make_request(
        "memory_write",
        "request-isolation-b",
        {"key": "project.b", "value": "B"},
    )
    await MemoryWriteHandler(memory_store)(first)
    await MemoryWriteHandler(memory_store)(second)

    await coordinator.abort(
        CleanupContext(
            first.task_id,
            first.step_id,
            first.request_id,
            first.tool_name,
        ),
        reason="cancel first only",
    )

    assert (await memory_store.get(first.request_id)).status is MemoryStatus.ROLLED_BACK
    assert (await memory_store.get(second.request_id)).status is MemoryStatus.PENDING
