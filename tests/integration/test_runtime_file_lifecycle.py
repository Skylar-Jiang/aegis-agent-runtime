from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ra_agent.audit import InMemoryAuditRecorder
from ra_agent.contracts import (
    DeepCheckResult,
    EffectStatus,
    ExecutionStatus,
    MemoryStatus,
    PolicyDecision,
    PostCheckResult,
    RiskLevel,
    RiskVerdict,
    RollbackPlan,
    SourceType,
    ToolCallRequest,
)
from ra_agent.execution.checkpoint import CheckpointStatus, FilesystemCheckpointManager
from ra_agent.execution.cleanup import RequestCleanupCoordinator
from ra_agent.execution.commit_gate import FilesystemCommitGate
from ra_agent.execution.effect_manager import EffectManager
from ra_agent.execution.effect_store import FilesystemEffectStore
from ra_agent.execution.executor import RegistryToolExecutor
from ra_agent.execution.pending_store import PendingStore
from ra_agent.execution.rollback import FilesystemRollbackManager
from ra_agent.execution.selective_rollback import SelectiveRollbackExecutor
from ra_agent.memory import FilesystemMemoryStore, MemoryLifecycleManager
from ra_agent.runtime.sandbox_flow import SandboxFlow
from ra_agent.security import MockPostExecutionChecker, MockPreExecutionChecker
from ra_agent.tools import DEFAULT_TOOL_SPECS, ToolRegistry
from ra_agent.tools.implementations.delete_file import DeleteFileHandler
from ra_agent.tools.implementations.memory_tools import MemoryWriteHandler
from ra_agent.tools.implementations.write_file import WriteFileHandler
from ra_agent.tools.path_resolver import SafePathResolver


class DeepChecker:
    def __init__(self, passed: bool = True) -> None:
        self.passed = passed

    async def check(self, request, result) -> DeepCheckResult:
        return DeepCheckResult(
            request_id=request.request_id, passed=self.passed, reason="test safety decision"
        )


class RejectingPostChecker:
    async def check(self, request, execution) -> PostCheckResult:
        return PostCheckResult(
            request_id=request.request_id, passed=False, reason="test post-check rejection"
        )


@dataclass
class FileRuntime:
    flow: SandboxFlow
    request: ToolCallRequest
    verdict: RiskVerdict
    target: Path
    pending: PendingStore
    checkpoints: FilesystemCheckpointManager
    effects: FilesystemEffectStore
    commit: FilesystemCommitGate
    rollback: FilesystemRollbackManager
    selective: SelectiveRollbackExecutor


def file_runtime(tmp_path: Path, tool: str = "write_file") -> FileRuntime:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "report.txt"
    target.write_text("original", encoding="utf-8")
    resolver = SafePathResolver(
        workspace, max_path_length=4096, max_read_bytes=1024, max_write_bytes=1024
    )
    pending = PendingStore(tmp_path / "pending")
    checkpoints = FilesystemCheckpointManager(tmp_path / "checkpoints", resolver)
    rollback = FilesystemRollbackManager(resolver, pending, checkpoints)
    effects = FilesystemEffectStore(tmp_path / "effects")
    manager = EffectManager(effects)
    cleanup = RequestCleanupCoordinator(
        pending_store=pending, rollback_manager=rollback, effect_manager=manager
    )
    registry = ToolRegistry()
    spec = next(spec for spec in DEFAULT_TOOL_SPECS if spec.name == tool)
    handler = WriteFileHandler if tool == "write_file" else DeleteFileHandler
    registry.register(spec, handler(resolver, pending))
    executor = RegistryToolExecutor(
        registry, pending, cleanup_coordinator=cleanup, effect_manager=manager
    )
    commit = FilesystemCommitGate(resolver, pending, checkpoints, manager)
    flow = SandboxFlow(
        checkpoint_manager=checkpoints,
        executor=executor,
        deep_checker=DeepChecker(),
        pre_checker=MockPreExecutionChecker(),
        post_checker=MockPostExecutionChecker(),
        commit_gate=commit,
        rollback_manager=rollback,
        audit_recorder=InMemoryAuditRecorder(),
        cleanup_coordinator=cleanup,
    )
    arguments = {"path": "report.txt"}
    if tool == "write_file":
        arguments["content"] = "candidate"
    request = ToolCallRequest(
        task_id="task-lifecycle",
        step_id="step-lifecycle",
        request_id="request-lifecycle",
        tool_name=tool,
        arguments=arguments,
        objective="update report",
        context_summary="file lifecycle regression",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
    )
    verdict = RiskVerdict(
        request_id=request.request_id,
        risk_level=RiskLevel.MEDIUM,
        recommended_decision=PolicyDecision.SANDBOX_CHECK,
        reason="file mutation",
    )
    selective = SelectiveRollbackExecutor(
        effect_store=effects,
        effect_manager=manager,
        checkpoint_manager=checkpoints,
        rollback_manager=rollback,
    )
    return FileRuntime(
        flow, request, verdict, target, pending, checkpoints, effects, commit, rollback, selective
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["deep", "post"])
async def test_rejected_file_rolls_back_effect_and_allows_idempotent_recovery(tmp_path, stage):
    runtime = file_runtime(tmp_path)
    if stage == "deep":
        runtime.flow.deep_checker = DeepChecker(False)
    else:
        runtime.flow.post_checker = RejectingPostChecker()

    result = await runtime.flow.run(runtime.request, runtime.verdict)

    assert result.status is ExecutionStatus.ROLLED_BACK
    assert runtime.target.read_text(encoding="utf-8") == "original"
    assert result.checkpoint_id is not None
    checkpoint = await runtime.checkpoints.get(result.checkpoint_id)
    assert checkpoint.status is CheckpointStatus.ROLLED_BACK
    effect = await runtime.effects.get_by_request_id(runtime.request.request_id)
    assert effect is not None
    assert effect.status is EffectStatus.ROLLED_BACK
    recovered = await runtime.selective.execute(
        RollbackPlan(
            plan_id="plan-recovery",
            task_id=runtime.request.task_id,
            trigger="retry recovery",
            effect_ids=[effect.effect_id],
            reason="repeat completed rollback safely",
        )
    )
    assert recovered.status is ExecutionStatus.SUCCESS
    assert recovered.rolled_back_request_ids == [runtime.request.request_id]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "phase"),
    [
        ("write_file", "file"),
        ("delete_file", "file"),
        ("write_file", "checkpoint"),
        ("write_file", "pending"),
        ("write_file", "stage"),
        ("delete_file", "stage"),
    ],
)
async def test_repeated_cancel_waits_for_commit_worker_before_rollback(
    tmp_path, monkeypatch, tool, phase
):
    runtime = file_runtime(tmp_path, tool)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    if phase == "file":
        owner = runtime.commit
        method = "_atomic_replace_target" if tool == "write_file" else "_commit_delete_sync"
    elif phase == "stage":
        owner = runtime.pending
        method = "_stage_write_sync" if tool == "write_file" else "_stage_delete_sync"
    else:
        owner = runtime.checkpoints if phase == "checkpoint" else runtime.pending
        method = "_mark_committed_sync"
    original = getattr(owner, method)

    def delayed_commit(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test did not release the commit worker")
        try:
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(owner, method, delayed_commit)
    task = asyncio.create_task(runtime.flow.run(runtime.request, runtime.verdict))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        for _ in range(3):
            task.cancel()
            # Let cancellation traverse the real coroutine stack.
            await asyncio.sleep(0)
        if phase == "stage":
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), 0.05)
        # Recovery must not race a worker that is still able to change the file.
        if phase == "file":
            checkpoint = await runtime.checkpoints.get("checkpoint-request-lifecycle")
            assert checkpoint.status is CheckpointStatus.CREATED
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(finished.wait, 5)

    assert runtime.target.read_text(encoding="utf-8") == "original"
    assert (await runtime.checkpoints.get("checkpoint-request-lifecycle")).status is (
        CheckpointStatus.ROLLED_BACK
    )
    effect = await runtime.effects.get_by_request_id(runtime.request.request_id)
    if phase == "stage":
        assert effect is None
    else:
        assert effect is not None and effect.status is EffectStatus.ROLLED_BACK
    assert not (runtime.pending.pending_root / runtime.request.request_id).exists()


@pytest.mark.asyncio
async def test_cancel_waits_for_checkpoint_worker_and_recovers_its_result(tmp_path, monkeypatch):
    runtime = file_runtime(tmp_path)
    original = runtime.checkpoints._create_sync
    entered = threading.Event()
    release = threading.Event()

    def delayed_checkpoint(request):
        checkpoint = original(request)
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test did not release checkpoint worker")
        return checkpoint

    monkeypatch.setattr(runtime.checkpoints, "_create_sync", delayed_checkpoint)
    task = asyncio.create_task(runtime.flow.run(runtime.request, runtime.verdict))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert runtime.target.read_text(encoding="utf-8") == "original"
    assert (await runtime.checkpoints.get("checkpoint-request-lifecycle")).status is (
        CheckpointStatus.ROLLED_BACK
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["stage", "post", "commit", "effect"])
async def test_memory_cancellation_waits_for_workers_and_rolls_back(tmp_path, monkeypatch, phase):
    runtime = file_runtime(tmp_path)
    memory = FilesystemMemoryStore(tmp_path / "memory", max_value_bytes=1024)
    manager = EffectManager(runtime.effects)
    cleanup = RequestCleanupCoordinator(
        memory_manager=MemoryLifecycleManager(memory, manager), effect_manager=manager
    )
    registry = ToolRegistry()
    registry.register(
        next(spec for spec in DEFAULT_TOOL_SPECS if spec.name == "memory_write"),
        MemoryWriteHandler(memory),
    )
    runtime.flow.executor = RegistryToolExecutor(
        registry,
        runtime.pending,
        memory_store=memory,
        cleanup_coordinator=cleanup,
        effect_manager=manager,
    )
    runtime.flow.cleanup_coordinator = cleanup
    request = runtime.request.model_copy(
        update={"tool_name": "memory_write", "arguments": {"key": "note", "value": "candidate"}}
    )
    entered = threading.Event()
    release = threading.Event()

    if phase == "post":
        class WaitingChecker:
            async def check(self, request, execution) -> PostCheckResult:
                entered.set()
                await asyncio.Event().wait()
                raise AssertionError("post check should have been cancelled")

        runtime.flow.post_checker = WaitingChecker()
    else:
        owner = runtime.effects if phase == "effect" else memory
        method = {
            "stage": "_stage_sync", "commit": "_mark_trusted_sync", "effect": "_register_sync"
        }[phase]
        original = getattr(owner, method)

        def delayed_update(*args):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("test did not release memory worker")
            return original(*args)

        monkeypatch.setattr(owner, method, delayed_update)
    task = asyncio.create_task(runtime.flow.run(request, runtime.verdict))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        if phase != "post":
            assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert (await memory.get(request.request_id)).status is MemoryStatus.ROLLED_BACK
    assert await memory.get_trusted("note") is None
    effect = await runtime.effects.get_by_request_id(request.request_id)
    if phase == "stage":
        assert effect is None
    else:
        assert effect is not None and effect.status is EffectStatus.ROLLED_BACK
    assert not (runtime.pending.pending_root / runtime.request.request_id).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["write_file", "delete_file"])
async def test_cancel_after_pending_cleanup_can_restore_committed_file(tmp_path, monkeypatch, tool):
    runtime = file_runtime(tmp_path, tool)
    cleanup = runtime.flow.cleanup_coordinator
    assert cleanup is not None
    original = cleanup.complete_filesystem_commit
    cleaned = asyncio.Event()
    release = asyncio.Event()

    async def delayed_cleanup(context):
        result = await original(context)
        cleaned.set()
        await release.wait()
        return result

    monkeypatch.setattr(cleanup, "complete_filesystem_commit", delayed_cleanup)
    task = asyncio.create_task(runtime.flow.run(runtime.request, runtime.verdict))
    await asyncio.wait_for(cleaned.wait(), 5)
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runtime.target.read_text(encoding="utf-8") == "original"
    effect = await runtime.effects.get_by_request_id(runtime.request.request_id)
    assert effect is not None and effect.status is EffectStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_repeated_cancel_during_rollback_waits_for_effect_update(tmp_path, monkeypatch):
    runtime = file_runtime(tmp_path)
    runtime.flow.deep_checker = DeepChecker(False)
    entered = threading.Event()
    release = threading.Event()
    original = runtime.rollback._restore_sync

    def delayed_restore(*args):
        entered.set()
        if not release.wait(5):
            raise TimeoutError("test did not release rollback")
        return original(*args)

    monkeypatch.setattr(runtime.rollback, "_restore_sync", delayed_restore)
    task = asyncio.create_task(runtime.flow.run(runtime.request, runtime.verdict))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert runtime.target.read_text(encoding="utf-8") == "original"
    effect = await runtime.effects.get_by_request_id(runtime.request.request_id)
    assert effect is not None and effect.status is EffectStatus.ROLLED_BACK
