from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from ra_agent.contracts import (
    DeepCheckResult,
    EffectStatus,
    ExecutionStatus,
    PostCheckResult,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
    ToolSpec,
)
from ra_agent.execution.artifacts import (
    build_quarantined_download_artifact,
    build_tool_output_artifact,
)
from ra_agent.execution.checkpoint import FilesystemCheckpointManager
from ra_agent.execution.cleanup import RequestCleanupCoordinator
from ra_agent.execution.commit_gate import FilesystemCommitGate
from ra_agent.execution.download_manager import DownloadLifecycleManager
from ra_agent.execution.effect_manager import EffectManager
from ra_agent.execution.effect_store import FilesystemEffectStore
from ra_agent.execution.executor import RegistryToolExecutor
from ra_agent.execution.pending_store import PendingStore
from ra_agent.execution.quarantine import FilesystemQuarantineStore
from ra_agent.execution.rollback import FilesystemRollbackManager
from ra_agent.memory import FilesystemMemoryStore, MemoryLifecycleManager
from ra_agent.tools.implementations.memory_tools import MemoryWriteHandler
from ra_agent.tools.implementations.write_file import WriteFileHandler
from ra_agent.tools.path_resolver import SafePathResolver
from ra_agent.tools.registry import ToolRegistry
from ra_agent.tools.specs import DEFAULT_TOOL_SPECS


def make_request(
    tool_name: str,
    request_id: str,
    arguments: dict[str, object],
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-1",
        step_id=f"step-{request_id}",
        request_id=request_id,
        tool_name=tool_name,
        arguments=arguments,
        objective="exercise unified effect facts",
        context_summary="effect integration test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def spec(name: str) -> ToolSpec:
    return next(item for item in DEFAULT_TOOL_SPECS if item.name == name)


@pytest.mark.asyncio
async def test_executor_registers_file_effect_after_checkpoint_binding(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resolver = SafePathResolver(
        workspace,
        max_path_length=512,
        max_read_bytes=4096,
        max_write_bytes=4096,
    )
    pending_store = PendingStore(tmp_path / "pending")
    effect_store = FilesystemEffectStore(tmp_path / "effects")
    effect_manager = EffectManager(effect_store)
    registry = ToolRegistry()
    registry.register(spec("write_file"), WriteFileHandler(resolver, pending_store))
    executor = RegistryToolExecutor(
        registry,
        pending_store,
        effect_manager=effect_manager,
    )
    request = make_request(
        "write_file",
        "request-file",
        {"path": "result.txt", "content": "safe content"},
    )

    execution = await executor.execute(request, checkpoint_id="checkpoint-file")
    effect = await effect_store.get_by_request_id(request.request_id)

    assert execution.checkpoint_id == "checkpoint-file"
    assert effect is not None
    assert effect.status is EffectStatus.PENDING
    assert effect.target_ref == "file:result.txt"
    assert effect.checkpoint_id == "checkpoint-file"
    assert not (workspace / "result.txt").exists()


@pytest.mark.asyncio
async def test_memory_lifecycle_updates_effect_after_real_state(tmp_path: Path) -> None:
    pending_store = PendingStore(tmp_path / "pending")
    memory_store = FilesystemMemoryStore(tmp_path / "memory", max_value_bytes=4096)
    effect_store = FilesystemEffectStore(tmp_path / "effects")
    effect_manager = EffectManager(effect_store)
    registry = ToolRegistry()
    registry.register(spec("memory_write"), MemoryWriteHandler(memory_store))
    executor = RegistryToolExecutor(
        registry,
        pending_store,
        memory_store,
        effect_manager=effect_manager,
    )
    request = make_request(
        "memory_write",
        "request-memory",
        {"key": "project.preference", "value": "formal"},
    )
    execution = await executor.execute(request)
    manager = MemoryLifecycleManager(memory_store, effect_manager)

    before = await effect_store.get_by_request_id(request.request_id)
    await manager.commit(
        request,
        execution,
        PostCheckResult(request_id=request.request_id, passed=True, reason="safe"),
    )
    after = await effect_store.get_by_request_id(request.request_id)

    assert before is not None and before.status is EffectStatus.PENDING
    assert after is not None and after.status is EffectStatus.COMMITTED
    assert await memory_store.get_trusted_value("project.preference") is not None


@pytest.mark.asyncio
async def test_memory_rejection_updates_effect_and_remains_unreadable(
    tmp_path: Path,
) -> None:
    pending_store = PendingStore(tmp_path / "pending")
    memory_store = FilesystemMemoryStore(tmp_path / "memory", max_value_bytes=4096)
    effect_store = FilesystemEffectStore(tmp_path / "effects")
    effect_manager = EffectManager(effect_store)
    registry = ToolRegistry()
    registry.register(spec("memory_write"), MemoryWriteHandler(memory_store))
    executor = RegistryToolExecutor(
        registry,
        pending_store,
        memory_store,
        effect_manager=effect_manager,
    )
    request = make_request(
        "memory_write",
        "request-memory-reject",
        {"key": "project.preference", "value": "poison"},
    )
    execution = await executor.execute(request)

    await MemoryLifecycleManager(memory_store, effect_manager).reject(
        request,
        execution,
        PostCheckResult(
            request_id=request.request_id, passed=False, reason="injection"
        ),
    )
    effect = await effect_store.get_by_request_id(request.request_id)

    assert effect is not None and effect.status is EffectStatus.REJECTED
    assert await memory_store.get_trusted_value("project.preference") is None


@pytest.mark.asyncio
async def test_download_lifecycle_updates_effect(tmp_path: Path) -> None:
    quarantine_store = FilesystemQuarantineStore(
        tmp_path / "quarantine",
        max_download_bytes=4096,
    )
    effect_store = FilesystemEffectStore(tmp_path / "effects")
    effect_manager = EffectManager(effect_store)
    request = make_request(
        "download_url",
        "request-download",
        {"url": "https://example.com/file"},
    )
    payload = b"safe download"
    temporary = quarantine_store.create_temporary_path(request.request_id)
    temporary.write_bytes(payload)
    record = await quarantine_store.stage(
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
    output = {
        "downloaded": True,
        "source_url": record.source_url,
        "final_url": record.final_url,
        "content_type": record.content_type,
        "size_bytes": record.size_bytes,
        "sha256": record.content_sha256,
        "status": record.status.value,
    }
    execution = ToolExecutionResult(
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        status=ExecutionStatus.PENDING_COMMIT,
        output=output,
        artifacts=[
            build_tool_output_artifact(
                request, output, status=ExecutionStatus.PENDING_COMMIT
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
    await effect_manager.register_pending(request, execution)

    await DownloadLifecycleManager(quarantine_store, effect_manager).commit(
        request,
        execution,
        PostCheckResult(request_id=request.request_id, passed=True, reason="safe"),
    )
    effect = await effect_store.get_by_request_id(request.request_id)

    assert effect is not None and effect.status is EffectStatus.COMMITTED


@pytest.mark.asyncio
async def test_filesystem_commit_updates_effect_only_after_real_commit(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "result.txt"
    target.write_text("old", encoding="utf-8")
    resolver = SafePathResolver(
        workspace,
        max_path_length=512,
        max_read_bytes=4096,
        max_write_bytes=4096,
    )
    pending_store = PendingStore(tmp_path / "pending")
    checkpoint_manager = FilesystemCheckpointManager(tmp_path / "checkpoints", resolver)
    effect_store = FilesystemEffectStore(tmp_path / "effects")
    effect_manager = EffectManager(effect_store)
    registry = ToolRegistry()
    registry.register(spec("write_file"), WriteFileHandler(resolver, pending_store))
    executor = RegistryToolExecutor(
        registry,
        pending_store,
        effect_manager=effect_manager,
    )
    request = make_request(
        "write_file",
        "request-file-commit",
        {"path": "result.txt", "content": "new"},
    )
    checkpoint = await checkpoint_manager.create(request)
    execution = await executor.execute(request, checkpoint_id=checkpoint.checkpoint_id)
    gate = FilesystemCommitGate(
        resolver,
        pending_store,
        checkpoint_manager,
        effect_manager,
    )

    before = await effect_store.get_by_request_id(request.request_id)
    result = await gate.commit(
        execution,
        DeepCheckResult(request_id=request.request_id, passed=True, reason="safe"),
    )
    after = await effect_store.get_by_request_id(request.request_id)

    assert before is not None and before.status is EffectStatus.PENDING
    assert result.status is ExecutionStatus.COMMITTED
    assert target.read_text(encoding="utf-8") == "new"
    assert after is not None and after.status is EffectStatus.COMMITTED


@pytest.mark.asyncio
async def test_filesystem_postcheck_rejection_marks_effect_rejected(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "result.txt"
    target.write_text("old", encoding="utf-8")
    resolver = SafePathResolver(
        workspace,
        max_path_length=512,
        max_read_bytes=4096,
        max_write_bytes=4096,
    )
    pending_store = PendingStore(tmp_path / "pending")
    checkpoint_manager = FilesystemCheckpointManager(tmp_path / "checkpoints", resolver)
    effect_store = FilesystemEffectStore(tmp_path / "effects")
    effect_manager = EffectManager(effect_store)
    registry = ToolRegistry()
    registry.register(spec("write_file"), WriteFileHandler(resolver, pending_store))
    executor = RegistryToolExecutor(
        registry,
        pending_store,
        effect_manager=effect_manager,
    )
    request = make_request(
        "write_file",
        "request-file-reject",
        {"path": "result.txt", "content": "unsafe"},
    )
    checkpoint = await checkpoint_manager.create(request)
    execution = await executor.execute(request, checkpoint_id=checkpoint.checkpoint_id)
    coordinator = RequestCleanupCoordinator(
        pending_store=pending_store,
        rollback_manager=FilesystemRollbackManager(
            resolver,
            pending_store,
            checkpoint_manager,
        ),
        effect_manager=effect_manager,
    )

    await coordinator.reject(
        request,
        execution,
        PostCheckResult(request_id=request.request_id, passed=False, reason="unsafe"),
    )
    effect = await effect_store.get_by_request_id(request.request_id)

    assert target.read_text(encoding="utf-8") == "old"
    assert effect is not None and effect.status is EffectStatus.REJECTED
