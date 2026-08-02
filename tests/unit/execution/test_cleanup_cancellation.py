from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ra_agent.contracts import (
    MemoryStatus,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
    ToolSpec,
)
from ra_agent.execution.cleanup import RequestCleanupCoordinator
from ra_agent.execution.executor import RegistryToolExecutor, ToolExecutionTimeoutError
from ra_agent.execution.pending_store import PendingRecord, PendingStore
from ra_agent.memory import FilesystemMemoryStore, MemoryLifecycleManager
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
        step_id="step-1",
        request_id=request_id,
        tool_name=tool_name,
        arguments=arguments,
        objective="cancel a pending operation safely",
        context_summary="cleanup cancellation unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def get_spec(tool_name: str) -> ToolSpec:
    return next(spec for spec in DEFAULT_TOOL_SPECS if spec.name == tool_name)


@pytest.mark.asyncio
async def test_write_cancelled_during_stage_cleans_pending_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resolver = SafePathResolver(
        workspace,
        max_path_length=4096,
        max_read_bytes=1024,
        max_write_bytes=1024,
    )
    store = PendingStore(tmp_path / "pending")
    request = make_request(
        "write_file",
        "request-cancel-write",
        {"path": "result.txt", "content": "candidate"},
    )
    original_stage = store.stage_write
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_stage(
        request_id: str,
        target_path: str,
        content: bytes,
    ) -> PendingRecord:
        started.set()
        await release.wait()
        return await original_stage(request_id, target_path, content)

    monkeypatch.setattr(store, "stage_write", delayed_stage)
    task = asyncio.create_task(WriteFileHandler(resolver, store)(request))
    await started.wait()
    task.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert not (store.pending_root / request.request_id).exists()
    assert not (workspace / "result.txt").exists()


class StageThenBlockMemoryHandler:
    def __init__(
        self,
        store: FilesystemMemoryStore,
        started: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        self._store = store
        self._started = started
        self._release = release

    async def __call__(self, request: ToolCallRequest) -> ToolExecutionResult:
        await self._store.stage(
            request,
            key="project.note",
            value="candidate",
        )
        self._started.set()
        await self._release.wait()
        raise AssertionError("handler should have been cancelled or timed out")


@pytest.mark.asyncio
async def test_executor_timeout_rolls_back_memory_staged_by_handler(
    tmp_path: Path,
) -> None:
    pending_store = PendingStore(tmp_path / "pending")
    memory_store = FilesystemMemoryStore(
        tmp_path / "memory",
        max_value_bytes=4096,
    )
    memory_manager = MemoryLifecycleManager(memory_store)
    coordinator = RequestCleanupCoordinator(
        memory_manager=memory_manager,
    )
    started = asyncio.Event()
    release = asyncio.Event()
    registry = ToolRegistry()

    # 给 Windows 文件系统足够时间完成 durable stage。
    # Handler 会在 stage 后永久等待，因此仍然必然触发 Executor timeout。
    spec = get_spec("memory_write").model_copy(update={"timeout_seconds": 1.0})
    registry.register(
        spec,
        StageThenBlockMemoryHandler(
            memory_store,
            started,
            release,
        ),
    )

    executor = RegistryToolExecutor(
        registry,
        pending_store,
        memory_store,
        cleanup_coordinator=coordinator,
    )
    request = make_request(
        "memory_write",
        "request-memory-timeout",
        {
            "key": "project.note",
            "value": "candidate",
        },
    )

    execution_task = asyncio.create_task(executor.execute(request))

    # 明确确认 Memory 已经 Stage，避免依赖磁盘速度。
    await asyncio.wait_for(
        started.wait(),
        timeout=2.0,
    )

    staged = await memory_store.get(request.request_id)
    assert staged.status is MemoryStatus.PENDING

    with pytest.raises(ToolExecutionTimeoutError):
        await execution_task

    rolled_back = await memory_store.get(request.request_id)
    assert rolled_back.status is MemoryStatus.ROLLED_BACK
    assert await memory_store.get_trusted("project.note") is None


@pytest.mark.asyncio
async def test_executor_cancellation_rolls_back_memory_staged_by_handler(
    tmp_path: Path,
) -> None:
    pending_store = PendingStore(tmp_path / "pending")
    memory_store = FilesystemMemoryStore(tmp_path / "memory", max_value_bytes=4096)
    memory_manager = MemoryLifecycleManager(memory_store)
    coordinator = RequestCleanupCoordinator(memory_manager=memory_manager)
    started = asyncio.Event()
    release = asyncio.Event()
    registry = ToolRegistry()
    registry.register(
        get_spec("memory_write"),
        StageThenBlockMemoryHandler(memory_store, started, release),
    )
    executor = RegistryToolExecutor(
        registry,
        pending_store,
        memory_store,
        cleanup_coordinator=coordinator,
    )
    request = make_request(
        "memory_write",
        "request-memory-cancel",
        {"key": "project.note", "value": "candidate"},
    )
    task = asyncio.create_task(executor.execute(request))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert (
        await memory_store.get(request.request_id)
    ).status is MemoryStatus.ROLLED_BACK
    assert await memory_store.get_trusted("project.note") is None


class StageThenFailMemoryHandler:
    def __init__(self, store: FilesystemMemoryStore) -> None:
        self._store = store

    async def __call__(self, request: ToolCallRequest) -> ToolExecutionResult:
        await self._store.stage(
            request,
            key="project.note",
            value="candidate",
        )
        raise OSError("simulated storage follow-up failure")


@pytest.mark.asyncio
async def test_executor_oserror_rolls_back_memory_staged_by_handler(
    tmp_path: Path,
) -> None:
    pending_store = PendingStore(tmp_path / "pending")
    memory_store = FilesystemMemoryStore(tmp_path / "memory", max_value_bytes=4096)
    memory_manager = MemoryLifecycleManager(memory_store)
    coordinator = RequestCleanupCoordinator(memory_manager=memory_manager)
    registry = ToolRegistry()
    registry.register(
        get_spec("memory_write"), StageThenFailMemoryHandler(memory_store)
    )
    executor = RegistryToolExecutor(
        registry,
        pending_store,
        memory_store,
        cleanup_coordinator=coordinator,
    )
    request = make_request(
        "memory_write",
        "request-memory-oserror",
        {"key": "project.note", "value": "candidate"},
    )

    with pytest.raises(OSError, match="simulated storage follow-up failure"):
        await executor.execute(request)

    assert (
        await memory_store.get(request.request_id)
    ).status is MemoryStatus.ROLLED_BACK
    assert await memory_store.get_trusted("project.note") is None
