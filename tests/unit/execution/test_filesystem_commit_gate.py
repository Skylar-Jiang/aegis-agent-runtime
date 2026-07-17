from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ra_agent.contracts import (
    DeepCheckResult,
    ExecutionStatus,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution.checkpoint import CheckpointStatus, FilesystemCheckpointManager
from ra_agent.execution.commit_gate import (
    CommitConflictError,
    CommitCorrelationError,
    CommitIntegrityError,
    CommitPreconditionError,
    FilesystemCommitGate,
    MockCommitGate,
)
from ra_agent.execution.pending_store import PendingStatus, PendingStore
from ra_agent.tools.implementations.delete_file import DeleteFileHandler
from ra_agent.tools.implementations.write_file import WriteFileHandler
from ra_agent.tools.path_resolver import SafePathResolver


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "reports").mkdir()
    (root / "reports" / "result.md").write_text("old content", encoding="utf-8")
    (root / "old.txt").write_text("delete me", encoding="utf-8")
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
def commit_gate(
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
) -> FilesystemCommitGate:
    return FilesystemCommitGate(resolver, pending_store, checkpoint_manager)


def make_request(
    tool_name: str,
    *,
    path: str,
    request_id: str,
    content: str | None = None,
) -> ToolCallRequest:
    arguments: dict[str, object] = {"path": path}
    if content is not None:
        arguments["content"] = content
    return ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id=request_id,
        tool_name=tool_name,
        arguments=arguments,
        objective="commit a verified pending file change",
        context_summary="filesystem commit gate unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def passed_deep_check(request_id: str) -> DeepCheckResult:
    return DeepCheckResult(
        request_id=request_id,
        passed=True,
        reason="pending change passed deep safety checks",
    )


async def prepare_execution(
    request: ToolCallRequest,
    *,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
) -> ToolExecutionResult:
    checkpoint = await checkpoint_manager.create(request)
    if request.tool_name == "write_file":
        execution = await WriteFileHandler(resolver, pending_store)(request)
    else:
        execution = await DeleteFileHandler(resolver, pending_store)(request)
    await pending_store.bind_checkpoint(request.request_id, checkpoint.checkpoint_id)
    return execution.model_copy(update={"checkpoint_id": checkpoint.checkpoint_id})


@pytest.mark.asyncio
async def test_commits_write_over_existing_file_and_marks_metadata(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    target = workspace / "reports" / "result.md"
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="new content",
        request_id="request-write-existing",
    )
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )

    result = await commit_gate.commit(execution, passed_deep_check(request.request_id))

    assert result.status is ExecutionStatus.COMMITTED
    assert result.request_id == request.request_id
    assert result.checkpoint_id == execution.checkpoint_id
    assert target.read_text(encoding="utf-8") == "new content"
    assert (
        await pending_store.get(request.request_id)
    ).status is PendingStatus.COMMITTED
    assert (
        await checkpoint_manager.get(execution.checkpoint_id or "")
    ).status is CheckpointStatus.COMMITTED
    assert not list(target.parent.glob(".result.md.*.tmp"))


@pytest.mark.asyncio
async def test_commits_write_to_new_file(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    target = workspace / "reports" / "new.md"
    request = make_request(
        "write_file",
        path="reports/new.md",
        content="created at commit",
        request_id="request-write-new",
    )
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    assert not target.exists()

    result = await commit_gate.commit(execution, passed_deep_check(request.request_id))

    assert result.status is ExecutionStatus.COMMITTED
    assert target.read_text(encoding="utf-8") == "created at commit"


@pytest.mark.asyncio
async def test_commits_delete_of_unchanged_file(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    target = workspace / "old.txt"
    request = make_request("delete_file", path="old.txt", request_id="request-delete")
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    assert target.is_file()

    result = await commit_gate.commit(execution, passed_deep_check(request.request_id))

    assert result.status is ExecutionStatus.COMMITTED
    assert not target.exists()
    assert (
        await pending_store.get(request.request_id)
    ).status is PendingStatus.COMMITTED
    assert (
        await checkpoint_manager.get(execution.checkpoint_id or "")
    ).status is CheckpointStatus.COMMITTED


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ExecutionStatus.SUCCESS, ExecutionStatus.FAILED])
async def test_rejects_non_pending_execution(
    commit_gate: FilesystemCommitGate,
    status: ExecutionStatus,
) -> None:
    execution = ToolExecutionResult(
        task_id="task-1",
        step_id="step-1",
        request_id="request-invalid-status",
        checkpoint_id="checkpoint-request-invalid-status",
        status=status,
    )
    with pytest.raises(CommitPreconditionError):
        await commit_gate.commit(execution, passed_deep_check(execution.request_id))


@pytest.mark.asyncio
async def test_rejects_failed_deep_check(commit_gate: FilesystemCommitGate) -> None:
    execution = ToolExecutionResult(
        task_id="task-1",
        step_id="step-1",
        request_id="request-deep-failed",
        checkpoint_id="checkpoint-request-deep-failed",
        status=ExecutionStatus.PENDING_COMMIT,
    )
    deep_check = DeepCheckResult(
        request_id=execution.request_id, passed=False, reason="unsafe"
    )
    with pytest.raises(CommitPreconditionError):
        await commit_gate.commit(execution, deep_check)


@pytest.mark.asyncio
async def test_rejects_missing_checkpoint_id(commit_gate: FilesystemCommitGate) -> None:
    execution = ToolExecutionResult(
        task_id="task-1",
        step_id="step-1",
        request_id="request-no-checkpoint",
        checkpoint_id=None,
        status=ExecutionStatus.PENDING_COMMIT,
    )
    with pytest.raises(CommitPreconditionError):
        await commit_gate.commit(execution, passed_deep_check(execution.request_id))


@pytest.mark.asyncio
async def test_rejects_deep_check_request_mismatch(
    commit_gate: FilesystemCommitGate,
) -> None:
    execution = ToolExecutionResult(
        task_id="task-1",
        step_id="step-1",
        request_id="request-a",
        checkpoint_id="checkpoint-request-a",
        status=ExecutionStatus.PENDING_COMMIT,
    )
    with pytest.raises(CommitCorrelationError):
        await commit_gate.commit(execution, passed_deep_check("request-b"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("checkpoint_id", "checkpoint-other"),
        ("tool_name", "delete_file"),
        ("target_path", "reports/other.md"),
    ],
)
async def test_rejects_pending_manifest_correlation_mismatch(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    field: str,
    replacement: str,
) -> None:
    request = make_request(
        "write_file",
        path="reports/new.md",
        content="content",
        request_id=f"request-pending-{field}",
    )
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    manifest_path = pending_store.pending_root / request.request_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = replacement
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(CommitCorrelationError):
        await commit_gate.commit(execution, passed_deep_check(request.request_id))


@pytest.mark.asyncio
async def test_rejects_checkpoint_manifest_request_mismatch(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
) -> None:
    request = make_request(
        "write_file",
        path="reports/new.md",
        content="content",
        request_id="request-checkpoint-mismatch",
    )
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    checkpoint_id = execution.checkpoint_id or ""
    manifest_path = checkpoint_manager.checkpoint_root / checkpoint_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["request_id"] = "request-other"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(CommitCorrelationError):
        await commit_gate.commit(execution, passed_deep_check(request.request_id))


@pytest.mark.asyncio
async def test_rejects_tampered_pending_payload_without_changing_workspace(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="new content",
        request_id="request-pending-tamper",
    )
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    (pending_store.pending_root / request.request_id / "payload.bin").write_bytes(
        b"tampered"
    )

    with pytest.raises(CommitIntegrityError):
        await commit_gate.commit(execution, passed_deep_check(request.request_id))

    assert (workspace / "reports" / "result.md").read_text(
        encoding="utf-8"
    ) == "old content"


@pytest.mark.asyncio
async def test_rejects_tampered_checkpoint_backup(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
) -> None:
    request = make_request(
        "delete_file", path="old.txt", request_id="request-backup-tamper"
    )
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    checkpoint_id = execution.checkpoint_id or ""
    (
        checkpoint_manager.checkpoint_root / checkpoint_id / "backups" / "0001.bin"
    ).write_bytes(b"tampered")

    with pytest.raises(CommitIntegrityError):
        await commit_gate.commit(execution, passed_deep_check(request.request_id))


@pytest.mark.asyncio
async def test_write_rejects_target_modified_after_checkpoint(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="agent content",
        request_id="request-write-conflict",
    )
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    target = workspace / "reports" / "result.md"
    target.write_text("user changed", encoding="utf-8")

    with pytest.raises(CommitConflictError):
        await commit_gate.commit(execution, passed_deep_check(request.request_id))

    assert target.read_text(encoding="utf-8") == "user changed"


@pytest.mark.asyncio
async def test_write_rejects_new_target_created_after_checkpoint(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    request = make_request(
        "write_file",
        path="reports/new.md",
        content="agent content",
        request_id="request-new-target-conflict",
    )
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    target = workspace / "reports" / "new.md"
    target.write_text("user created", encoding="utf-8")

    with pytest.raises(CommitConflictError):
        await commit_gate.commit(execution, passed_deep_check(request.request_id))

    assert target.read_text(encoding="utf-8") == "user created"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["modified", "missing"])
async def test_delete_rejects_target_changed_after_checkpoint(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
    change: str,
) -> None:
    request = make_request(
        "delete_file",
        path="old.txt",
        request_id=f"request-delete-{change}",
    )
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    target = workspace / "old.txt"
    if change == "modified":
        target.write_text("user changed", encoding="utf-8")
    else:
        target.unlink()

    with pytest.raises(CommitConflictError):
        await commit_gate.commit(execution, passed_deep_check(request.request_id))

    if change == "modified":
        assert target.read_text(encoding="utf-8") == "user changed"


@pytest.mark.asyncio
async def test_repeated_committed_request_is_idempotent(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="new content",
        request_id="request-idempotent-commit",
    )
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    deep_check = passed_deep_check(request.request_id)

    first = await commit_gate.commit(execution, deep_check)
    second = await commit_gate.commit(execution, deep_check)

    assert first == second
    assert (workspace / "reports" / "result.md").read_text(
        encoding="utf-8"
    ) == "new content"


@pytest.mark.asyncio
async def test_rejects_inconsistent_committed_metadata(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
) -> None:
    request = make_request(
        "write_file",
        path="reports/new.md",
        content="content",
        request_id="request-inconsistent-status",
    )
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    await pending_store.mark_committed(request.request_id)

    with pytest.raises(CommitPreconditionError):
        await commit_gate.commit(execution, passed_deep_check(request.request_id))


@pytest.mark.asyncio
async def test_write_rejects_symlink_swap_when_supported(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
    tmp_path: Path,
) -> None:
    request = make_request(
        "write_file",
        path="reports/new.md",
        content="agent content",
        request_id="request-symlink-swap",
    )
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = workspace / "reports" / "new.md"
    try:
        link.symlink_to(outside)
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symbolic links are unavailable: {error}")

    with pytest.raises(CommitConflictError):
        await commit_gate.commit(execution, passed_deep_check(request.request_id))

    assert outside.read_text(encoding="utf-8") == "outside"


@pytest.mark.asyncio
async def test_write_rejects_existing_target_removed_after_checkpoint(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="agent content",
        request_id="request-existing-target-missing",
    )
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    (workspace / "reports" / "result.md").unlink()

    with pytest.raises(CommitConflictError):
        await commit_gate.commit(execution, passed_deep_check(request.request_id))


@pytest.mark.asyncio
async def test_mock_commit_gate_returns_committed_result() -> None:
    execution = ToolExecutionResult(
        task_id="task-1",
        step_id="step-1",
        request_id="request-mock",
        checkpoint_id="checkpoint-request-mock",
        status=ExecutionStatus.PENDING_COMMIT,
    )

    result = await MockCommitGate().commit(
        execution,
        passed_deep_check(execution.request_id),
    )

    assert result.status is ExecutionStatus.COMMITTED
    assert result.request_id == execution.request_id
    assert result.checkpoint_id == execution.checkpoint_id


@pytest.mark.asyncio
async def test_rejects_operation_and_tool_name_mismatch(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
) -> None:
    request = make_request(
        "write_file",
        path="reports/new.md",
        content="content",
        request_id="request-operation-tool-mismatch",
    )
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )

    pending_manifest = pending_store.pending_root / request.request_id / "manifest.json"
    pending_data = json.loads(pending_manifest.read_text(encoding="utf-8"))
    pending_data["tool_name"] = "delete_file"
    pending_manifest.write_text(json.dumps(pending_data), encoding="utf-8")

    checkpoint_id = execution.checkpoint_id or ""
    checkpoint_manifest = (
        checkpoint_manager.checkpoint_root / checkpoint_id / "manifest.json"
    )
    checkpoint_data = json.loads(checkpoint_manifest.read_text(encoding="utf-8"))
    checkpoint_data["tool_name"] = "delete_file"
    checkpoint_manifest.write_text(json.dumps(checkpoint_data), encoding="utf-8")

    with pytest.raises(CommitCorrelationError):
        await commit_gate.commit(execution, passed_deep_check(request.request_id))


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX mode preservation is not meaningful on Windows",
)
@pytest.mark.asyncio
async def test_write_preserves_existing_posix_mode(
    commit_gate: FilesystemCommitGate,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    target = workspace / "reports" / "result.md"
    os.chmod(target, 0o600)
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="new content",
        request_id="request-preserve-mode",
    )
    execution = await prepare_execution(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )

    await commit_gate.commit(execution, passed_deep_check(request.request_id))

    assert target.stat().st_mode & 0o777 == 0o600
