from __future__ import annotations

import json
import os
import stat
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
from ra_agent.execution._temp_files import transaction_temp_token
from ra_agent.execution.checkpoint import (
    CheckpointConflictError,
    CheckpointIntegrityError,
    CheckpointStatus,
    FilesystemCheckpointManager,
)
from ra_agent.execution.commit_gate import FilesystemCommitGate
from ra_agent.execution.pending_store import PendingStore
from ra_agent.execution.rollback import (
    FilesystemRollbackManager,
    MockRollbackManager,
    RollbackConflictError,
    RollbackCorrelationError,
    RollbackIntegrityError,
)
from ra_agent.tools.implementations.delete_file import DeleteFileHandler
from ra_agent.tools.implementations.write_file import WriteFileHandler
from ra_agent.tools.path_resolver import SafePathResolver


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "reports").mkdir()
    (root / "reports" / "result.md").write_text(
        "old content",
        encoding="utf-8",
    )
    (root / "old.txt").write_text(
        "delete me",
        encoding="utf-8",
    )
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
    return FilesystemCheckpointManager(
        tmp_path / "checkpoints",
        resolver,
    )


@pytest.fixture
def commit_gate(
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
) -> FilesystemCommitGate:
    return FilesystemCommitGate(
        resolver,
        pending_store,
        checkpoint_manager,
    )


@pytest.fixture
def rollback_manager(
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
) -> FilesystemRollbackManager:
    return FilesystemRollbackManager(
        resolver,
        pending_store,
        checkpoint_manager,
    )


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
        objective="restore a failed filesystem operation",
        context_summary="filesystem rollback manager unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def passed_deep_check(request_id: str) -> DeepCheckResult:
    return DeepCheckResult(
        request_id=request_id,
        passed=True,
        reason="safe for commit",
    )


async def prepare_pending(
    request: ToolCallRequest,
    *,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
) -> ToolExecutionResult:
    checkpoint = await checkpoint_manager.create(request)

    if request.tool_name == "write_file":
        execution = await WriteFileHandler(
            resolver,
            pending_store,
        )(request)
    else:
        execution = await DeleteFileHandler(
            resolver,
            pending_store,
        )(request)

    await pending_store.bind_checkpoint(
        request.request_id,
        checkpoint.checkpoint_id,
    )

    return execution.model_copy(update={"checkpoint_id": checkpoint.checkpoint_id})


async def prepare_committed(
    request: ToolCallRequest,
    *,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
) -> ToolExecutionResult:
    execution = await prepare_pending(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    await commit_gate.commit(
        execution,
        passed_deep_check(request.request_id),
    )
    return execution


@pytest.mark.asyncio
async def test_pending_write_before_commit_keeps_original_and_cleans_pending(
    rollback_manager: FilesystemRollbackManager,
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
        request_id="request-write-before-commit",
    )
    execution = await prepare_pending(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )

    result = await rollback_manager.rollback(
        execution.checkpoint_id or "",
        request.request_id,
    )

    assert result.status is ExecutionStatus.ROLLED_BACK
    assert target.read_text(encoding="utf-8") == "old content"
    assert not (pending_store.pending_root / request.request_id).exists()
    checkpoint = await checkpoint_manager.get(execution.checkpoint_id or "")
    assert checkpoint.status is CheckpointStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_pending_new_write_before_commit_remains_absent(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    target = workspace / "reports" / "new.md"
    request = make_request(
        "write_file",
        path="reports/new.md",
        content="new content",
        request_id="request-new-before-commit",
    )
    execution = await prepare_pending(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )

    await rollback_manager.rollback(
        execution.checkpoint_id or "",
        request.request_id,
    )

    assert not target.exists()
    assert not (pending_store.pending_root / request.request_id).exists()


@pytest.mark.asyncio
async def test_restores_existing_file_after_committed_write(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
) -> None:
    target = workspace / "reports" / "result.md"
    original = target.read_bytes()
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="committed content",
        request_id="request-write-after-commit",
    )
    execution = await prepare_committed(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        commit_gate=commit_gate,
    )
    assert target.read_text(encoding="utf-8") == "committed content"

    result = await rollback_manager.rollback(
        execution.checkpoint_id or "",
        request.request_id,
    )

    assert result.status is ExecutionStatus.ROLLED_BACK
    assert target.read_bytes() == original
    assert not (pending_store.pending_root / request.request_id).exists()
    checkpoint = await checkpoint_manager.get(execution.checkpoint_id or "")
    assert checkpoint.status is CheckpointStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_committed_write_rollback_rejects_later_workspace_edit(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
) -> None:
    target = workspace / "reports" / "result.md"
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="committed content",
        request_id="request-write-later-edit",
    )
    execution = await prepare_committed(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        commit_gate=commit_gate,
    )
    target.write_text("legitimate later edit", encoding="utf-8")

    with pytest.raises(RollbackConflictError):
        await rollback_manager.rollback(
            execution.checkpoint_id or "",
            request.request_id,
        )

    assert target.read_text(encoding="utf-8") == "legitimate later edit"


@pytest.mark.asyncio
async def test_committed_write_rollback_preserves_edit_racing_after_validation(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "reports" / "result.md"
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="committed content",
        request_id="request-write-race",
    )
    execution = await prepare_committed(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        commit_gate=commit_gate,
    )
    original_restore = rollback_manager._restore_file_atomically

    def edit_before_restore(
        restore_target: Path,
        payload: bytes,
        *,
        request_id: str,
        mode: int,
        mtime_ns: int,
    ) -> None:
        restore_target.write_text("racing later edit", encoding="utf-8")
        original_restore(
            restore_target,
            payload,
            request_id=request_id,
            mode=mode,
            mtime_ns=mtime_ns,
        )

    monkeypatch.setattr(rollback_manager, "_restore_file_atomically", edit_before_restore)

    with pytest.raises(RollbackConflictError):
        await rollback_manager.rollback(
            execution.checkpoint_id or "",
            request.request_id,
        )

    assert target.read_text(encoding="utf-8") == "racing later edit"
    token = transaction_temp_token(request.request_id)
    assert list(target.parent.glob(f".result.md.{token}.rollback-*"))


@pytest.mark.asyncio
async def test_retry_recovers_interruption_after_custody_before_backup_install(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "reports" / "result.md"
    original = target.read_bytes()
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="committed content",
        request_id="request-interrupt-before-install",
    )
    execution = await prepare_committed(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        commit_gate=commit_gate,
    )
    original_restore = rollback_manager._restore_file_atomically

    def interrupt_before_install(
        restore_target: Path,
        payload: bytes,
        *,
        request_id: str,
        mode: int,
        mtime_ns: int,
    ) -> None:
        raise OSError("simulated interruption before backup install")

    monkeypatch.setattr(
        rollback_manager,
        "_restore_file_atomically",
        interrupt_before_install,
    )

    with pytest.raises(OSError, match="before backup install"):
        await rollback_manager.rollback(
            execution.checkpoint_id or "",
            request.request_id,
        )

    token = transaction_temp_token(request.request_id)
    quarantine = target.parent / f".result.md.{token}.rollback-quarantine"
    quarantine_was_retained = quarantine.is_dir()
    assert not target.exists()

    monkeypatch.setattr(
        rollback_manager,
        "_restore_file_atomically",
        original_restore,
    )
    result = await rollback_manager.rollback(
        execution.checkpoint_id or "",
        request.request_id,
    )

    assert quarantine_was_retained
    assert result.status is ExecutionStatus.ROLLED_BACK
    assert target.read_bytes() == original
    assert not quarantine.exists()


@pytest.mark.asyncio
async def test_retry_finishes_interruption_after_backup_install_before_quarantine_discard(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "reports" / "result.md"
    original = target.read_bytes()
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="committed content",
        request_id="request-interrupt-after-install",
    )
    execution = await prepare_committed(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        commit_gate=commit_gate,
    )
    original_restore = rollback_manager._restore_file_atomically

    def interrupt_after_install(
        restore_target: Path,
        payload: bytes,
        *,
        request_id: str,
        mode: int,
        mtime_ns: int,
    ) -> None:
        original_restore(
            restore_target,
            payload,
            request_id=request_id,
            mode=mode,
            mtime_ns=mtime_ns,
        )
        raise OSError("simulated interruption after backup install")

    monkeypatch.setattr(
        rollback_manager,
        "_restore_file_atomically",
        interrupt_after_install,
    )

    with pytest.raises(OSError, match="after backup install"):
        await rollback_manager.rollback(
            execution.checkpoint_id or "",
            request.request_id,
        )

    token = transaction_temp_token(request.request_id)
    quarantine = target.parent / f".result.md.{token}.rollback-quarantine"
    quarantine_was_retained = quarantine.is_dir()
    assert target.read_bytes() == original

    monkeypatch.setattr(
        rollback_manager,
        "_restore_file_atomically",
        original_restore,
    )
    result = await rollback_manager.rollback(
        execution.checkpoint_id or "",
        request.request_id,
    )

    assert quarantine_was_retained
    assert result.status is ExecutionStatus.ROLLED_BACK
    assert target.read_bytes() == original
    assert not quarantine.exists()


@pytest.mark.asyncio
async def test_retry_finishes_interruption_between_quarantine_file_and_directory_cleanup(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "reports" / "result.md"
    original = target.read_bytes()
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="committed content",
        request_id="request-interrupt-during-quarantine-cleanup",
    )
    execution = await prepare_committed(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        commit_gate=commit_gate,
    )
    original_discard = rollback_manager._discard_quarantine

    def interrupt_after_quarantine_file_removal(quarantined: Path | None) -> None:
        assert quarantined is not None
        quarantined.unlink()
        raise OSError("simulated interruption during quarantine cleanup")

    monkeypatch.setattr(
        rollback_manager,
        "_discard_quarantine",
        interrupt_after_quarantine_file_removal,
    )

    with pytest.raises(OSError, match="during quarantine cleanup"):
        await rollback_manager.rollback(
            execution.checkpoint_id or "",
            request.request_id,
        )

    token = transaction_temp_token(request.request_id)
    quarantine = target.parent / f".result.md.{token}.rollback-quarantine"
    assert target.read_bytes() == original
    assert quarantine.is_dir()
    assert not any(quarantine.iterdir())

    monkeypatch.setattr(rollback_manager, "_discard_quarantine", original_discard)
    result = await rollback_manager.rollback(
        execution.checkpoint_id or "",
        request.request_id,
    )

    assert result.status is ExecutionStatus.ROLLED_BACK
    assert target.read_bytes() == original
    assert not quarantine.exists()


@pytest.mark.asyncio
async def test_removes_new_file_created_by_commit(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
) -> None:
    target = workspace / "reports" / "new.md"
    request = make_request(
        "write_file",
        path="reports/new.md",
        content="committed content",
        request_id="request-new-after-commit",
    )
    execution = await prepare_committed(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        commit_gate=commit_gate,
    )
    assert target.is_file()

    await rollback_manager.rollback(
        execution.checkpoint_id or "",
        request.request_id,
    )

    assert not target.exists()


@pytest.mark.asyncio
async def test_delete_before_commit_keeps_file_and_cleans_marker(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    target = workspace / "old.txt"
    request = make_request(
        "delete_file",
        path="old.txt",
        request_id="request-delete-before-commit",
    )
    execution = await prepare_pending(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )

    await rollback_manager.rollback(
        execution.checkpoint_id or "",
        request.request_id,
    )

    assert target.read_text(encoding="utf-8") == "delete me"
    assert not (pending_store.pending_root / request.request_id).exists()


@pytest.mark.asyncio
async def test_restores_file_after_committed_delete(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
) -> None:
    target = workspace / "old.txt"
    original = target.read_bytes()
    request = make_request(
        "delete_file",
        path="old.txt",
        request_id="request-delete-after-commit",
    )
    execution = await prepare_committed(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        commit_gate=commit_gate,
    )
    assert not target.exists()

    await rollback_manager.rollback(
        execution.checkpoint_id or "",
        request.request_id,
    )

    assert target.read_bytes() == original
    assert not (pending_store.pending_root / request.request_id).exists()


@pytest.mark.asyncio
async def test_committed_delete_rollback_rejects_later_recreated_file(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
) -> None:
    target = workspace / "old.txt"
    request = make_request(
        "delete_file",
        path="old.txt",
        request_id="request-delete-later-recreate",
    )
    execution = await prepare_committed(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        commit_gate=commit_gate,
    )
    target.write_text("legitimate replacement", encoding="utf-8")

    with pytest.raises(RollbackConflictError):
        await rollback_manager.rollback(
            execution.checkpoint_id or "",
            request.request_id,
        )

    assert target.read_text(encoding="utf-8") == "legitimate replacement"


@pytest.mark.asyncio
async def test_committed_delete_rollback_preserves_recreate_racing_after_validation(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "old.txt"
    request = make_request(
        "delete_file",
        path="old.txt",
        request_id="request-delete-race",
    )
    execution = await prepare_committed(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        commit_gate=commit_gate,
    )
    original_restore = rollback_manager._restore_file_atomically

    def recreate_before_restore(
        restore_target: Path,
        payload: bytes,
        *,
        request_id: str,
        mode: int,
        mtime_ns: int,
    ) -> None:
        restore_target.write_text("racing recreation", encoding="utf-8")
        original_restore(
            restore_target,
            payload,
            request_id=request_id,
            mode=mode,
            mtime_ns=mtime_ns,
        )

    monkeypatch.setattr(
        rollback_manager,
        "_restore_file_atomically",
        recreate_before_restore,
    )

    with pytest.raises(RollbackConflictError):
        await rollback_manager.rollback(
            execution.checkpoint_id or "",
            request.request_id,
        )

    assert target.read_text(encoding="utf-8") == "racing recreation"
    token = transaction_temp_token(request.request_id)
    assert list(target.parent.glob(f".old.txt.{token}.rollback-*"))


@pytest.mark.asyncio
async def test_repeated_rollback_is_idempotent_after_pending_cleanup(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
) -> None:
    target = workspace / "reports" / "result.md"
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="new content",
        request_id="request-repeat",
    )
    execution = await prepare_committed(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        commit_gate=commit_gate,
    )

    first = await rollback_manager.rollback(
        execution.checkpoint_id or "",
        request.request_id,
    )
    first_stat = target.stat()
    second = await rollback_manager.rollback(
        execution.checkpoint_id or "",
        request.request_id,
    )
    second_stat = target.stat()

    assert first.status is ExecutionStatus.ROLLED_BACK
    assert second.status is ExecutionStatus.ROLLED_BACK
    assert target.read_text(encoding="utf-8") == "old content"
    assert first_stat.st_size == second_stat.st_size
    assert first_stat.st_mtime_ns == second_stat.st_mtime_ns


@pytest.mark.asyncio
async def test_checkpoint_only_rollback_succeeds_when_executor_never_staged(
    rollback_manager: FilesystemRollbackManager,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="unused",
        request_id="request-no-pending",
    )
    checkpoint = await checkpoint_manager.create(request)

    result = await rollback_manager.rollback(
        checkpoint.checkpoint_id,
        request.request_id,
    )

    assert result.status is ExecutionStatus.ROLLED_BACK
    assert (workspace / "reports" / "result.md").read_text(
        encoding="utf-8"
    ) == "old content"
    record = await checkpoint_manager.get(checkpoint.checkpoint_id)
    assert record.status is CheckpointStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_checkpoint_only_new_write_rollback_keeps_target_absent(
    rollback_manager: FilesystemRollbackManager,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    request = make_request(
        "write_file",
        path="reports/new.md",
        content="unused",
        request_id="request-no-pending-new",
    )
    checkpoint = await checkpoint_manager.create(request)

    await rollback_manager.rollback(
        checkpoint.checkpoint_id,
        request.request_id,
    )

    assert not (workspace / "reports" / "new.md").exists()


@pytest.mark.asyncio
async def test_rejects_checkpoint_request_mismatch_without_touching_workspace(
    rollback_manager: FilesystemRollbackManager,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="unused",
        request_id="request-correlation",
    )
    checkpoint = await checkpoint_manager.create(request)

    with pytest.raises(RollbackCorrelationError):
        await rollback_manager.rollback(
            checkpoint.checkpoint_id,
            "request-other",
        )

    assert (workspace / "reports" / "result.md").read_text(
        encoding="utf-8"
    ) == "old content"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("checkpoint_id", "checkpoint-other"),
        ("tool_name", "delete_file"),
        ("target_path", "reports/other.md"),
    ],
)
async def test_rejects_pending_correlation_mismatch(
    rollback_manager: FilesystemRollbackManager,
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
    execution = await prepare_pending(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    manifest_path = pending_store.pending_root / request.request_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = replacement
    manifest_path.write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

    with pytest.raises(RollbackCorrelationError):
        await rollback_manager.rollback(
            execution.checkpoint_id or "",
            request.request_id,
        )


@pytest.mark.asyncio
async def test_rejects_tampered_checkpoint_backup_without_restoring(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
) -> None:
    target = workspace / "reports" / "result.md"
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="new content",
        request_id="request-tampered-backup",
    )
    execution = await prepare_committed(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        commit_gate=commit_gate,
    )
    checkpoint_id = execution.checkpoint_id or ""
    (
        checkpoint_manager.checkpoint_root / checkpoint_id / "backups" / "0001.bin"
    ).write_bytes(b"tampered")

    with pytest.raises(RollbackIntegrityError):
        await rollback_manager.rollback(
            checkpoint_id,
            request.request_id,
        )

    assert target.read_text(encoding="utf-8") == "new content"


@pytest.mark.asyncio
async def test_rejects_corrupted_checkpoint_manifest(
    rollback_manager: FilesystemRollbackManager,
    checkpoint_manager: FilesystemCheckpointManager,
) -> None:
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="unused",
        request_id="request-corrupt-manifest",
    )
    checkpoint = await checkpoint_manager.create(request)
    manifest_path = (
        checkpoint_manager.checkpoint_root / checkpoint.checkpoint_id / "manifest.json"
    )
    manifest_path.write_text("{", encoding="utf-8")

    with pytest.raises(CheckpointIntegrityError):
        await rollback_manager.rollback(
            checkpoint.checkpoint_id,
            request.request_id,
        )


@pytest.mark.asyncio
async def test_rejects_tampered_pending_payload(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
) -> None:
    request = make_request(
        "write_file",
        path="reports/new.md",
        content="content",
        request_id="request-pending-tamper",
    )
    execution = await prepare_pending(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    (pending_store.pending_root / request.request_id / "payload.bin").write_bytes(
        b"tampered"
    )

    with pytest.raises(RollbackIntegrityError):
        await rollback_manager.rollback(
            execution.checkpoint_id or "",
            request.request_id,
        )


@pytest.mark.asyncio
async def test_cleans_only_this_transaction_commit_temp_files(
    rollback_manager: FilesystemRollbackManager,
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
        request_id="request-temp-cleanup",
    )
    execution = await prepare_pending(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    token = transaction_temp_token(request.request_id)
    owned = target.parent / f".result.md.{token}.orphan.tmp"
    unrelated = target.parent / ".result.md.unrelated.tmp"
    owned.write_bytes(b"partial")
    unrelated.write_bytes(b"keep")

    await rollback_manager.rollback(
        execution.checkpoint_id or "",
        request.request_id,
    )

    assert not owned.exists()
    assert unrelated.read_bytes() == b"keep"


@pytest.mark.asyncio
async def test_temp_cleanup_does_not_match_prefix_related_request_id(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "reports" / "result.md"
    original_replace = os.replace
    original_unlink = Path.unlink

    async def leave_commit_temp(request_id: str, content: str) -> ToolExecutionResult:
        request = make_request(
            "write_file",
            path="reports/result.md",
            content=content,
            request_id=request_id,
        )
        execution = await prepare_pending(
            request,
            resolver=resolver,
            pending_store=pending_store,
            checkpoint_manager=checkpoint_manager,
        )
        existing_temps = set(target.parent.glob(".result.md.*.tmp"))

        def fail_target_replace(
            source: str,
            destination: str | os.PathLike[str],
        ) -> None:
            if Path(destination) == target:
                raise OSError("simulated commit replace failure")
            original_replace(source, destination)

        def preserve_commit_temp(path: Path, *, missing_ok: bool = False) -> None:
            if path.parent == target.parent and path.name.endswith(".tmp"):
                return
            original_unlink(path, missing_ok=missing_ok)

        with monkeypatch.context() as patcher:
            patcher.setattr(os, "replace", fail_target_replace)
            patcher.setattr(Path, "unlink", preserve_commit_temp)

            with pytest.raises(OSError, match="simulated commit replace failure"):
                await commit_gate.commit(
                    execution,
                    passed_deep_check(request_id),
                )

        new_temps = set(target.parent.glob(".result.md.*.tmp")) - existing_temps
        assert len(new_temps) == 1
        return execution

    await leave_commit_temp("abc.def", "other transaction")
    related_temp = next(target.parent.glob(".result.md.*.tmp"))
    execution = await leave_commit_temp("abc", "owned transaction")
    owned_temp = next(
        temp
        for temp in target.parent.glob(".result.md.*.tmp")
        if temp != related_temp
    )

    await rollback_manager.rollback(
        execution.checkpoint_id or "",
        "abc",
    )

    assert not owned_temp.exists()
    assert related_temp.read_bytes() == b"other transaction"


def test_temp_cleanup_ignores_owned_file_disappearing_before_unlink(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "reports" / "result.md"
    request_id = "request-temp-disappears"
    token = transaction_temp_token(request_id)
    owned = target.parent / f".result.md.{token}.orphan.tmp"
    owned.write_bytes(b"partial")
    original_unlink = Path.unlink

    def disappear_before_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path == owned and path.exists():
            original_unlink(path)
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", disappear_before_unlink)

    FilesystemRollbackManager._cleanup_commit_temp_files(target, request_id)

    assert not owned.exists()


@pytest.mark.asyncio
async def test_commit_failure_after_workspace_write_can_be_rolled_back(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "reports" / "result.md"
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="partially committed",
        request_id="request-immediate-commit-failure",
    )
    execution = await prepare_pending(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )

    async def fail_mark_committed(request_id: str) -> None:
        raise OSError(f"simulated metadata failure for {request_id}")

    monkeypatch.setattr(pending_store, "mark_committed", fail_mark_committed)

    with pytest.raises(OSError, match="simulated metadata failure"):
        await commit_gate.commit(
            execution,
            passed_deep_check(request.request_id),
        )

    assert target.read_text(encoding="utf-8") == "partially committed"

    await rollback_manager.rollback(
        execution.checkpoint_id or "",
        request.request_id,
    )

    assert target.read_text(encoding="utf-8") == "old content"


@pytest.mark.asyncio
async def test_restores_original_mode_and_mtime(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
) -> None:
    target = workspace / "reports" / "result.md"
    requested_mtime = 1_700_000_000_123_456_789
    os.utime(
        target,
        ns=(requested_mtime, requested_mtime),
    )

    # Filesystems may round timestamps to their supported precision.
    # Use the value actually persisted before creating the checkpoint.
    expected_mtime = target.stat().st_mtime_ns

    if os.name != "nt":
        target.chmod(0o640)

    request = make_request(
        "write_file",
        path="reports/result.md",
        content="new content",
        request_id="request-metadata",
    )
    execution = await prepare_committed(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        commit_gate=commit_gate,
    )

    await rollback_manager.rollback(
        execution.checkpoint_id or "",
        request.request_id,
    )

    restored = target.stat()
    assert restored.st_mtime_ns == expected_mtime

    if os.name != "nt":
        assert stat.S_IMODE(restored.st_mode) == 0o640


@pytest.mark.asyncio
async def test_symlink_swap_is_rejected(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    workspace: Path,
    tmp_path: Path,
) -> None:
    target = workspace / "reports" / "result.md"
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="new content",
        request_id="request-symlink",
    )
    execution = await prepare_pending(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    target.unlink()

    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are not available in this environment")

    with pytest.raises(RollbackConflictError):
        await rollback_manager.rollback(
            execution.checkpoint_id or "",
            request.request_id,
        )

    assert outside.read_text(encoding="utf-8") == "outside"


@pytest.mark.asyncio
async def test_failed_pending_cleanup_can_be_retried(
    rollback_manager: FilesystemRollbackManager,
    resolver: SafePathResolver,
    pending_store: PendingStore,
    checkpoint_manager: FilesystemCheckpointManager,
    commit_gate: FilesystemCommitGate,
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = workspace / "reports" / "result.md"
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="new content",
        request_id="request-retry-cleanup",
    )
    execution = await prepare_committed(
        request,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        commit_gate=commit_gate,
    )

    original_cleanup = pending_store.cleanup
    call_count = 0

    async def fail_once(request_id: str) -> None:
        nonlocal call_count
        call_count += 1

        if call_count == 1:
            raise OSError("simulated cleanup failure")

        await original_cleanup(request_id)

    monkeypatch.setattr(pending_store, "cleanup", fail_once)

    with pytest.raises(OSError, match="simulated cleanup failure"):
        await rollback_manager.rollback(
            execution.checkpoint_id or "",
            request.request_id,
        )

    assert target.read_text(encoding="utf-8") == "old content"
    checkpoint = await checkpoint_manager.get(execution.checkpoint_id or "")
    assert checkpoint.status is CheckpointStatus.COMMITTED

    result = await rollback_manager.rollback(
        execution.checkpoint_id or "",
        request.request_id,
    )

    assert result.status is ExecutionStatus.ROLLED_BACK
    checkpoint = await checkpoint_manager.get(execution.checkpoint_id or "")
    assert checkpoint.status is CheckpointStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_mark_committed_rejects_rolled_back_checkpoint(
    rollback_manager: FilesystemRollbackManager,
    checkpoint_manager: FilesystemCheckpointManager,
) -> None:
    request = make_request(
        "write_file",
        path="reports/result.md",
        content="unused",
        request_id="request-no-recommit",
    )
    checkpoint = await checkpoint_manager.create(request)
    await rollback_manager.rollback(
        checkpoint.checkpoint_id,
        request.request_id,
    )

    with pytest.raises(CheckpointConflictError):
        await checkpoint_manager.mark_committed(checkpoint.checkpoint_id)


@pytest.mark.asyncio
async def test_mock_rollback_manager_returns_rolled_back() -> None:
    result = await MockRollbackManager().rollback(
        "checkpoint-request-1",
        "request-1",
    )

    assert result.status is ExecutionStatus.ROLLED_BACK
    assert result.checkpoint_id == "checkpoint-request-1"
    assert result.request_id == "request-1"
