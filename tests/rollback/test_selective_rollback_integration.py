from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from ra_agent.contracts import (
    EffectStatus,
    ExecutionStatus,
    PostCheckResult,
    RollbackPlan,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution.artifacts import (
    build_quarantined_download_artifact,
    build_tool_output_artifact,
)
from ra_agent.execution.download_manager import DownloadLifecycleManager
from ra_agent.execution.effect_manager import EffectManager
from ra_agent.execution.effect_store import FilesystemEffectStore
from ra_agent.execution.quarantine import (
    FilesystemQuarantineStore,
    QuarantineStatus,
)
from ra_agent.execution.selective_rollback import (
    RollbackPlanValidationError,
    SelectiveRollbackExecutor,
)
from ra_agent.memory import FilesystemMemoryStore, MemoryLifecycleManager
from ra_agent.tools.implementations.memory_tools import MemoryWriteHandler


def request(
    tool_name: str,
    request_id: str,
    arguments: dict[str, object],
    *,
    step_id: str | None = None,
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-1",
        step_id=step_id or f"step-{request_id}",
        request_id=request_id,
        tool_name=tool_name,
        arguments=arguments,
        objective="exercise selective rollback",
        context_summary="selective rollback integration test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def rollback_plan(
    plan_id: str,
    *,
    effect_ids: list[str] | None = None,
    request_ids: list[str] | None = None,
) -> RollbackPlan:
    return RollbackPlan(
        plan_id=plan_id,
        task_id="task-1",
        trigger="postcheck_failure",
        effect_ids=effect_ids or [],
        request_ids=request_ids or [],
        checkpoint_ids=[],
        reason="selective rollback test",
    )


async def commit_memory(
    *,
    memory_store: FilesystemMemoryStore,
    effect_manager: EffectManager,
    lifecycle: MemoryLifecycleManager,
    request_id: str,
    key: str,
    value: object,
) -> str:
    tool_request = request(
        "memory_write",
        request_id,
        {"key": key, "value": value},
    )
    execution = await MemoryWriteHandler(memory_store)(tool_request)
    effect = await effect_manager.register_pending(tool_request, execution)
    await lifecycle.commit(
        tool_request,
        execution,
        PostCheckResult(
            request_id=request_id,
            passed=True,
            reason="safe",
        ),
    )
    return effect.effect_id


async def stage_memory(
    *,
    memory_store: FilesystemMemoryStore,
    effect_manager: EffectManager,
    request_id: str,
    key: str,
    value: object,
) -> str:
    tool_request = request(
        "memory_write",
        request_id,
        {"key": key, "value": value},
    )
    execution = await MemoryWriteHandler(memory_store)(tool_request)
    effect = await effect_manager.register_pending(tool_request, execution)
    return effect.effect_id


async def commit_download(
    *,
    quarantine_store: FilesystemQuarantineStore,
    effect_manager: EffectManager,
    lifecycle: DownloadLifecycleManager,
    request_id: str,
    payload: bytes,
) -> str:
    source_url = f"https://example.com/{request_id}"
    tool_request = request(
        "download_url",
        request_id,
        {"url": source_url},
    )
    temporary = quarantine_store.create_temporary_path(request_id)
    temporary.write_bytes(payload)
    record = await quarantine_store.stage(
        tool_request,
        source_url=source_url,
        final_url=source_url,
        redirect_chain=(),
        temporary_path=temporary,
        content_type="application/octet-stream",
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
        task_id=tool_request.task_id,
        step_id=tool_request.step_id,
        request_id=tool_request.request_id,
        status=ExecutionStatus.PENDING_COMMIT,
        output=output,
        artifacts=[
            build_tool_output_artifact(
                tool_request,
                output,
                status=ExecutionStatus.PENDING_COMMIT,
            ),
            build_quarantined_download_artifact(
                tool_request,
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
    effect = await effect_manager.register_pending(tool_request, execution)
    await lifecycle.commit(
        tool_request,
        execution,
        PostCheckResult(
            request_id=request_id,
            passed=True,
            reason="safe",
        ),
    )
    return effect.effect_id


@pytest.mark.asyncio
async def test_memory_selective_rollback_restores_previous_trusted_version(
    tmp_path: Path,
) -> None:
    effect_store = FilesystemEffectStore(tmp_path / "effects")
    effect_manager = EffectManager(effect_store)
    memory_store = FilesystemMemoryStore(
        tmp_path / "memory",
        max_value_bytes=4096,
    )
    lifecycle = MemoryLifecycleManager(memory_store, effect_manager)
    old_effect = await commit_memory(
        memory_store=memory_store,
        effect_manager=effect_manager,
        lifecycle=lifecycle,
        request_id="request-memory-old",
        key="project.preference",
        value="formal",
    )
    new_effect = await commit_memory(
        memory_store=memory_store,
        effect_manager=effect_manager,
        lifecycle=lifecycle,
        request_id="request-memory-new",
        key="project.preference",
        value="poisoned",
    )
    unrelated_effect = await commit_memory(
        memory_store=memory_store,
        effect_manager=effect_manager,
        lifecycle=lifecycle,
        request_id="request-memory-other",
        key="project.language",
        value="zh-CN",
    )
    runner = SelectiveRollbackExecutor(
        effect_store=effect_store,
        effect_manager=effect_manager,
        memory_manager=lifecycle,
    )

    result = await runner.execute(rollback_plan("plan-memory", effect_ids=[new_effect]))

    assert result.status is ExecutionStatus.SUCCESS
    assert result.rolled_back_request_ids == ["request-memory-new"]
    trusted_preference = await memory_store.get_trusted_value("project.preference")
    trusted_language = await memory_store.get_trusted_value("project.language")
    assert trusted_preference is not None
    assert trusted_language is not None
    assert trusted_preference[1] == "formal"
    assert trusted_language[1] == "zh-CN"
    assert (await effect_store.get(old_effect)).status is EffectStatus.COMMITTED
    assert (await effect_store.get(new_effect)).status is EffectStatus.ROLLED_BACK
    assert (await effect_store.get(unrelated_effect)).status is EffectStatus.COMMITTED


@pytest.mark.asyncio
async def test_pending_memory_effect_can_be_selectively_rolled_back(
    tmp_path: Path,
) -> None:
    effect_store = FilesystemEffectStore(tmp_path / "effects")
    effect_manager = EffectManager(effect_store)
    memory_store = FilesystemMemoryStore(
        tmp_path / "memory",
        max_value_bytes=4096,
    )
    lifecycle = MemoryLifecycleManager(memory_store, effect_manager)
    effect_id = await stage_memory(
        memory_store=memory_store,
        effect_manager=effect_manager,
        request_id="request-memory-pending",
        key="project.pending",
        value="candidate",
    )
    runner = SelectiveRollbackExecutor(
        effect_store=effect_store,
        effect_manager=effect_manager,
        memory_manager=lifecycle,
    )

    result = await runner.execute(
        rollback_plan("plan-pending-memory", request_ids=["request-memory-pending"])
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert (await effect_store.get(effect_id)).status is EffectStatus.ROLLED_BACK
    assert await memory_store.get_trusted_value("project.pending") is None


@pytest.mark.asyncio
async def test_download_rollback_does_not_touch_independent_download(
    tmp_path: Path,
) -> None:
    effect_store = FilesystemEffectStore(tmp_path / "effects")
    effect_manager = EffectManager(effect_store)
    quarantine_store = FilesystemQuarantineStore(
        tmp_path / "quarantine",
        max_download_bytes=4096,
    )
    lifecycle = DownloadLifecycleManager(quarantine_store, effect_manager)
    first = await commit_download(
        quarantine_store=quarantine_store,
        effect_manager=effect_manager,
        lifecycle=lifecycle,
        request_id="request-download-a",
        payload=b"download-a",
    )
    second = await commit_download(
        quarantine_store=quarantine_store,
        effect_manager=effect_manager,
        lifecycle=lifecycle,
        request_id="request-download-b",
        payload=b"download-b",
    )
    runner = SelectiveRollbackExecutor(
        effect_store=effect_store,
        effect_manager=effect_manager,
        download_manager=lifecycle,
    )

    result = await runner.execute(rollback_plan("plan-download", effect_ids=[second]))

    assert result.status is ExecutionStatus.SUCCESS
    assert (
        await quarantine_store.get("request-download-a")
    ).status is QuarantineStatus.COMMITTED
    assert (
        await quarantine_store.get("request-download-b")
    ).status is QuarantineStatus.ROLLED_BACK
    assert (await effect_store.get(first)).status is EffectStatus.COMMITTED
    assert (await effect_store.get(second)).status is EffectStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_missing_request_scope_fails_before_existing_effect_changes(
    tmp_path: Path,
) -> None:
    effect_store = FilesystemEffectStore(tmp_path / "effects")
    effect_manager = EffectManager(effect_store)
    memory_store = FilesystemMemoryStore(
        tmp_path / "memory",
        max_value_bytes=4096,
    )
    lifecycle = MemoryLifecycleManager(memory_store, effect_manager)
    effect_id = await commit_memory(
        memory_store=memory_store,
        effect_manager=effect_manager,
        lifecycle=lifecycle,
        request_id="request-existing",
        key="project.note",
        value="keep",
    )
    runner = SelectiveRollbackExecutor(
        effect_store=effect_store,
        effect_manager=effect_manager,
        memory_manager=lifecycle,
    )

    with pytest.raises(RollbackPlanValidationError):
        await runner.execute(
            rollback_plan(
                "plan-missing",
                effect_ids=[effect_id],
                request_ids=["request-missing"],
            )
        )

    assert (await effect_store.get(effect_id)).status is EffectStatus.COMMITTED
    trusted_note = await memory_store.get_trusted_value("project.note")
    assert trusted_note is not None
    assert trusted_note[1] == "keep"


@pytest.mark.asyncio
async def test_same_target_rollbacks_are_serialized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    effect_store = FilesystemEffectStore(tmp_path / "effects")
    effect_manager = EffectManager(effect_store)
    memory_store = FilesystemMemoryStore(
        tmp_path / "memory",
        max_value_bytes=4096,
    )
    lifecycle = MemoryLifecycleManager(memory_store, effect_manager)
    old_effect = await commit_memory(
        memory_store=memory_store,
        effect_manager=effect_manager,
        lifecycle=lifecycle,
        request_id="request-lock-old",
        key="project.locked",
        value="old",
    )
    new_effect = await commit_memory(
        memory_store=memory_store,
        effect_manager=effect_manager,
        lifecycle=lifecycle,
        request_id="request-lock-new",
        key="project.locked",
        value="new",
    )
    active = 0
    max_active = 0
    original = lifecycle.rollback

    async def observed_rollback(request_id: str, *, reason: str):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        try:
            return await original(request_id, reason=reason)
        finally:
            active -= 1

    monkeypatch.setattr(lifecycle, "rollback", observed_rollback)
    runner = SelectiveRollbackExecutor(
        effect_store=effect_store,
        effect_manager=effect_manager,
        memory_manager=lifecycle,
    )

    old_result, new_result = await asyncio.gather(
        runner.execute(rollback_plan("plan-lock-old", effect_ids=[old_effect])),
        runner.execute(rollback_plan("plan-lock-new", effect_ids=[new_effect])),
    )

    assert old_result.status is ExecutionStatus.SUCCESS
    assert new_result.status is ExecutionStatus.SUCCESS
    assert max_active == 1
    assert await memory_store.get_trusted_value("project.locked") is None
