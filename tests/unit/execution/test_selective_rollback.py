from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ra_agent.contracts import (
    DeepCheckResult,
    EffectRecord,
    EffectStatus,
    ExecutionStatus,
    RollbackPlan,
    RollbackResult,
    SourceType,
    ToolCallRequest,
)
from ra_agent.execution.checkpoint import FilesystemCheckpointManager
from ra_agent.execution.commit_gate import FilesystemCommitGate
from ra_agent.execution.effect_manager import (
    FILE_WRITE_EFFECT,
    EffectManager,
)
from ra_agent.execution.effect_store import FilesystemEffectStore
from ra_agent.execution.pending_store import PendingStore
from ra_agent.execution.rollback import (
    CommittedEffectState,
    FilesystemRollbackManager,
)
from ra_agent.execution.selective_rollback import (
    RollbackPlanValidationError,
    SelectiveRollbackExecutor,
)
from ra_agent.runtime import RollbackPlanExecutor
from ra_agent.tools.implementations.write_file import WriteFileHandler
from ra_agent.tools.path_resolver import SafePathResolver


@dataclass(slots=True)
class FileEnvironment:
    workspace: Path
    resolver: SafePathResolver
    pending_store: PendingStore
    checkpoint_manager: FilesystemCheckpointManager
    effect_store: FilesystemEffectStore
    effect_manager: EffectManager
    rollback_manager: FilesystemRollbackManager
    commit_gate: FilesystemCommitGate


def make_request(
    request_id: str,
    path: str,
    content: str,
    *,
    task_id: str = "task-1",
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id=task_id,
        step_id=f"step-{request_id}",
        request_id=request_id,
        tool_name="write_file",
        arguments={"path": path, "content": content},
        objective="test selective rollback",
        context_summary="selective rollback unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def make_environment(tmp_path: Path) -> FileEnvironment:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    resolver = SafePathResolver(
        workspace,
        max_path_length=512,
        max_read_bytes=4096,
        max_write_bytes=4096,
    )
    pending_store = PendingStore(tmp_path / "pending")
    checkpoint_manager = FilesystemCheckpointManager(
        tmp_path / "checkpoints",
        resolver,
    )
    effect_store = FilesystemEffectStore(tmp_path / "effects")
    effect_manager = EffectManager(effect_store)
    rollback_manager = FilesystemRollbackManager(
        resolver,
        pending_store,
        checkpoint_manager,
    )
    commit_gate = FilesystemCommitGate(
        resolver,
        pending_store,
        checkpoint_manager,
        effect_manager,
    )
    return FileEnvironment(
        workspace=workspace,
        resolver=resolver,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        effect_store=effect_store,
        effect_manager=effect_manager,
        rollback_manager=rollback_manager,
        commit_gate=commit_gate,
    )


async def commit_file(
    environment: FileEnvironment,
    request: ToolCallRequest,
) -> EffectRecord:
    checkpoint = await environment.checkpoint_manager.create(request)
    execution = await WriteFileHandler(
        environment.resolver,
        environment.pending_store,
    )(request)
    await environment.pending_store.bind_checkpoint(
        request.request_id,
        checkpoint.checkpoint_id,
    )
    execution = execution.model_copy(update={"checkpoint_id": checkpoint.checkpoint_id})
    await environment.effect_manager.register_pending(request, execution)
    result = await environment.commit_gate.commit(
        execution,
        DeepCheckResult(
            request_id=request.request_id,
            passed=True,
            reason="safe",
        ),
    )
    assert result.status is ExecutionStatus.COMMITTED
    # Production cleanup may remove pending data after commit. Selective rollback
    # must still identify the committed state from the Effect artifact reference.
    await environment.pending_store.cleanup(request.request_id)
    effect = await environment.effect_store.get_by_request_id(request.request_id)
    assert effect is not None
    return effect


def executor(environment: FileEnvironment) -> SelectiveRollbackExecutor:
    return SelectiveRollbackExecutor(
        effect_store=environment.effect_store,
        effect_manager=environment.effect_manager,
        checkpoint_manager=environment.checkpoint_manager,
        rollback_manager=environment.rollback_manager,
    )


def plan(
    *,
    plan_id: str,
    task_id: str = "task-1",
    effect_ids: list[str] | None = None,
    request_ids: list[str] | None = None,
    checkpoint_ids: list[str] | None = None,
) -> RollbackPlan:
    return RollbackPlan(
        plan_id=plan_id,
        task_id=task_id,
        trigger="test",
        effect_ids=effect_ids or [],
        request_ids=request_ids or [],
        checkpoint_ids=checkpoint_ids or [],
        reason="test rollback",
    )


@pytest.mark.asyncio
async def test_only_explicit_effect_is_rolled_back(tmp_path: Path) -> None:
    environment = make_environment(tmp_path)
    (environment.workspace / "a.txt").write_text("old-a", encoding="utf-8")
    (environment.workspace / "b.txt").write_text("old-b", encoding="utf-8")
    effect_a = await commit_file(
        environment,
        make_request("request-a", "a.txt", "new-a"),
    )
    effect_b = await commit_file(
        environment,
        make_request("request-b", "b.txt", "new-b"),
    )

    result = await executor(environment).execute(
        plan(plan_id="plan-b", effect_ids=[effect_b.effect_id])
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert result.rolled_back_request_ids == ["request-b"]
    assert result.failed_request_ids == []
    assert (environment.workspace / "a.txt").read_text(encoding="utf-8") == "new-a"
    assert (environment.workspace / "b.txt").read_text(encoding="utf-8") == "old-b"
    assert (
        await environment.effect_store.get(effect_a.effect_id)
    ).status is EffectStatus.COMMITTED
    assert (
        await environment.effect_store.get(effect_b.effect_id)
    ).status is EffectStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_effect_request_and_checkpoint_scope_execute_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = make_environment(tmp_path)
    (environment.workspace / "item.txt").write_text("old", encoding="utf-8")
    effect = await commit_file(
        environment,
        make_request("request-item", "item.txt", "new"),
    )
    calls = 0
    original = environment.rollback_manager.rollback

    async def counted_rollback(
        checkpoint_id: str,
        request_id: str,
        *,
        committed_effect: CommittedEffectState | None = None,
    ) -> RollbackResult:
        nonlocal calls
        calls += 1
        return await original(
            checkpoint_id,
            request_id,
            committed_effect=committed_effect,
        )

    monkeypatch.setattr(environment.rollback_manager, "rollback", counted_rollback)

    result = await executor(environment).execute(
        plan(
            plan_id="plan-deduplicated",
            effect_ids=[effect.effect_id],
            request_ids=[effect.request_id],
            checkpoint_ids=[effect.checkpoint_id or ""],
        )
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert calls == 1


@pytest.mark.asyncio
async def test_external_effect_rejected_before_any_rollback(tmp_path: Path) -> None:
    environment = make_environment(tmp_path)
    (environment.workspace / "local.txt").write_text("old-local", encoding="utf-8")
    (environment.workspace / "external.txt").write_text(
        "old-external",
        encoding="utf-8",
    )
    local = await commit_file(
        environment,
        make_request("request-local", "local.txt", "new-local"),
    )
    external = await commit_file(
        environment,
        make_request(
            "request-external",
            "external.txt",
            "new-external",
            task_id="task-2",
        ),
    )

    with pytest.raises(RollbackPlanValidationError, match="another task"):
        await executor(environment).execute(
            plan(
                plan_id="plan-external",
                task_id="task-1",
                effect_ids=[local.effect_id, external.effect_id],
            )
        )

    assert (environment.workspace / "local.txt").read_text(
        encoding="utf-8"
    ) == "new-local"
    assert (environment.workspace / "external.txt").read_text(
        encoding="utf-8"
    ) == "new-external"
    assert (
        await environment.effect_store.get(local.effect_id)
    ).status is EffectStatus.COMMITTED
    assert (
        await environment.effect_store.get(external.effect_id)
    ).status is EffectStatus.COMMITTED


@pytest.mark.asyncio
async def test_checkpoint_effect_mismatch_fails_preflight(tmp_path: Path) -> None:
    environment = make_environment(tmp_path)
    (environment.workspace / "safe.txt").write_text("old", encoding="utf-8")
    request = make_request("request-safe", "safe.txt", "new")
    checkpoint = await environment.checkpoint_manager.create(request)
    invalid = EffectRecord(
        effect_id="11111111-1111-5111-8111-111111111111",
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        kind=FILE_WRITE_EFFECT,
        target_ref="file:different.txt",
        status=EffectStatus.COMMITTED,
        checkpoint_id=checkpoint.checkpoint_id,
        artifact_refs=["artifact:request-safe:pending_file:" + "a" * 64 + ":3"],
        created_at=datetime.now(UTC),
    )
    await environment.effect_store.register(invalid)
    await environment.checkpoint_manager.mark_committed(checkpoint.checkpoint_id)

    with pytest.raises(
        RollbackPlanValidationError,
        match="target_ref",
    ):
        await executor(environment).execute(
            plan(plan_id="plan-mismatch", effect_ids=[invalid.effect_id])
        )

    assert (environment.workspace / "safe.txt").read_text(encoding="utf-8") == "old"


@pytest.mark.asyncio
async def test_partial_failure_preserves_conflict_and_rolls_back_other_target(
    tmp_path: Path,
) -> None:
    environment = make_environment(tmp_path)
    (environment.workspace / "a.txt").write_text("old-a", encoding="utf-8")
    (environment.workspace / "b.txt").write_text("old-b", encoding="utf-8")
    effect_a = await commit_file(
        environment,
        make_request("request-a", "a.txt", "new-a"),
    )
    effect_b = await commit_file(
        environment,
        make_request("request-b", "b.txt", "new-b"),
    )
    (environment.workspace / "b.txt").write_text(
        "user-change",
        encoding="utf-8",
    )

    result = await executor(environment).execute(
        plan(
            plan_id="plan-partial",
            effect_ids=[effect_a.effect_id, effect_b.effect_id],
        )
    )

    assert result.status is ExecutionStatus.FAILED
    assert result.rolled_back_request_ids == ["request-a"]
    assert result.failed_request_ids == ["request-b"]
    assert (environment.workspace / "a.txt").read_text(encoding="utf-8") == "old-a"
    assert (environment.workspace / "b.txt").read_text(
        encoding="utf-8"
    ) == "user-change"
    assert (
        await environment.effect_store.get(effect_a.effect_id)
    ).status is EffectStatus.ROLLED_BACK
    assert (
        await environment.effect_store.get(effect_b.effect_id)
    ).status is EffectStatus.COMMITTED


@pytest.mark.asyncio
async def test_repeated_plan_is_idempotent(tmp_path: Path) -> None:
    environment = make_environment(tmp_path)
    (environment.workspace / "item.txt").write_text("old", encoding="utf-8")
    effect = await commit_file(
        environment,
        make_request("request-repeat", "item.txt", "new"),
    )
    rollback_plan = plan(
        plan_id="plan-repeat",
        effect_ids=[effect.effect_id],
    )
    runner = executor(environment)

    first = await runner.execute(rollback_plan)
    second = await runner.execute(rollback_plan)

    assert first.status is ExecutionStatus.SUCCESS
    assert second.status is ExecutionStatus.SUCCESS
    assert first.rolled_back_request_ids == ["request-repeat"]
    assert second.rolled_back_request_ids == ["request-repeat"]
    assert (environment.workspace / "item.txt").read_text(encoding="utf-8") == "old"


def test_selective_rollback_executor_implements_frozen_protocol(tmp_path: Path) -> None:
    environment = make_environment(tmp_path)

    assert isinstance(executor(environment), RollbackPlanExecutor)
