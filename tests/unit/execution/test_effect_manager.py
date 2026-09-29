from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from ra_agent.contracts import (
    EffectStatus,
    ExecutionStatus,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution.artifacts import (
    build_pending_delete_artifact,
    build_pending_file_artifact,
    build_pending_memory_artifact,
    build_quarantined_download_artifact,
    build_tool_output_artifact,
)
from ra_agent.execution.effect_manager import (
    DOWNLOAD_EFFECT,
    FILE_DELETE_EFFECT,
    FILE_WRITE_EFFECT,
    MEMORY_WRITE_EFFECT,
    EffectManager,
)
from ra_agent.execution.effect_store import EffectConflictError, FilesystemEffectStore
from ra_agent.execution.pending_store import (
    PendingOperation,
    PendingRecord,
    PendingStatus,
)


def request(tool_name: str, request_id: str = "request-1") -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id=request_id,
        tool_name=tool_name,
        arguments={},
        objective="test effect facts",
        context_summary="unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def file_execution(
    tool_name: str,
    *,
    request_id: str = "request-1",
    target_path: str = "backend/result.txt",
    checkpoint_id: str = "checkpoint-1",
) -> tuple[ToolCallRequest, ToolExecutionResult]:
    tool_request = request(tool_name, request_id)
    if tool_name == "write_file":
        payload = b"safe"
        record = PendingRecord(
            request_id=request_id,
            checkpoint_id=checkpoint_id,
            tool_name=tool_name,
            operation=PendingOperation.WRITE,
            target_path=target_path,
            pending_path=f"{request_id}/payload.bin",
            content_sha256=sha256(payload).hexdigest(),
            size_bytes=len(payload),
            created_at=datetime.now(UTC),
            status=PendingStatus.PENDING,
        )
        effect_artifact = build_pending_file_artifact(tool_request, record)
        output = {"staged": True, "target_path": target_path}
    else:
        record = PendingRecord(
            request_id=request_id,
            checkpoint_id=checkpoint_id,
            tool_name=tool_name,
            operation=PendingOperation.DELETE,
            target_path=target_path,
            pending_path=None,
            content_sha256=None,
            size_bytes=None,
            created_at=datetime.now(UTC),
            status=PendingStatus.PENDING,
        )
        effect_artifact = build_pending_delete_artifact(tool_request, record)
        output = {"staged": True, "target_path": target_path}
    execution = ToolExecutionResult(
        task_id=tool_request.task_id,
        step_id=tool_request.step_id,
        request_id=tool_request.request_id,
        checkpoint_id=checkpoint_id,
        status=ExecutionStatus.PENDING_COMMIT,
        output=output,
        artifacts=[
            build_tool_output_artifact(
                tool_request,
                output,
                status=ExecutionStatus.PENDING_COMMIT,
            ),
            effect_artifact,
        ],
    )
    return tool_request, execution


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "kind"),
    [("write_file", FILE_WRITE_EFFECT), ("delete_file", FILE_DELETE_EFFECT)],
)
async def test_registers_filesystem_effect_with_safe_target(
    tmp_path: Path,
    tool_name: str,
    kind: str,
) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))
    tool_request, execution = file_execution(tool_name)

    effect = await manager.register_pending(tool_request, execution)

    assert effect.kind == kind
    assert effect.target_ref == "file:backend/result.txt"
    assert effect.checkpoint_id == "checkpoint-1"
    assert effect.status is EffectStatus.PENDING
    assert len(effect.artifact_refs) == 1
    assert "safe" not in effect.artifact_refs[0]


@pytest.mark.asyncio
async def test_registers_memory_effect_without_raw_value(tmp_path: Path) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))
    tool_request = request("memory_write")
    digest = sha256(b'"secret-memory-value"').hexdigest()
    output = {"staged": True, "key": "project.preference"}
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
            build_pending_memory_artifact(
                tool_request,
                memory_id="memory-1",
                key="project.preference",
                path="records/request-1/payload.json",
                content_sha256=digest,
                size_bytes=21,
            ),
        ],
    )

    effect = await manager.register_pending(tool_request, execution)

    assert effect.kind == MEMORY_WRITE_EFFECT
    assert effect.target_ref == "memory:project.preference"
    assert effect.checkpoint_id is None
    serialized = effect.model_dump_json()
    assert "secret-memory-value" not in serialized


@pytest.mark.asyncio
async def test_registers_download_effect_without_content_or_absolute_path(
    tmp_path: Path,
) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))
    tool_request = request("download_url")
    output = {"downloaded": True}
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
                quarantine_path="request-1/payload.bin",
                source_url="https://example.com/file",
                final_url="https://example.com/file",
                content_sha256="b" * 64,
                size_bytes=10,
                content_type="application/octet-stream",
            ),
        ],
    )

    effect = await manager.register_pending(tool_request, execution)

    assert effect.kind == DOWNLOAD_EFFECT
    assert effect.target_ref == "download:request-1"
    assert "payload.bin" not in effect.target_ref


@pytest.mark.asyncio
async def test_effect_id_is_deterministic_for_idempotent_retry(tmp_path: Path) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))
    tool_request, execution = file_execution("write_file")

    first = await manager.register_pending(tool_request, execution)
    second = await manager.register_pending(tool_request, execution)

    assert second.effect_id == first.effect_id
    assert second.created_at == first.created_at


@pytest.mark.asyncio
async def test_same_request_with_different_target_fails_closed(tmp_path: Path) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))
    first_request, first_execution = file_execution("write_file")
    await manager.register_pending(first_request, first_execution)
    second_request, second_execution = file_execution(
        "write_file",
        target_path="backend/other.txt",
    )

    with pytest.raises(EffectConflictError):
        await manager.register_pending(second_request, second_execution)


@pytest.mark.asyncio
async def test_effect_transitions_follow_v04_statuses(tmp_path: Path) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))
    tool_request, execution = file_execution("write_file")
    await manager.register_pending(tool_request, execution)

    committed = await manager.mark_committed(
        tool_request.request_id,
        checkpoint_id="checkpoint-1",
    )
    rolled_back = await manager.mark_rolled_back(tool_request.request_id)

    assert committed.status is EffectStatus.COMMITTED
    assert rolled_back is not None
    assert rolled_back.status is EffectStatus.ROLLED_BACK


@pytest.mark.asyncio
async def test_reject_and_clean_transitions_use_frozen_effect_statuses(
    tmp_path: Path,
) -> None:
    reject_manager = EffectManager(FilesystemEffectStore(tmp_path / "reject-effects"))
    reject_request, reject_execution = file_execution(
        "write_file", request_id="request-reject"
    )
    await reject_manager.register_pending(reject_request, reject_execution)

    rejected = await reject_manager.mark_rejected(reject_request.request_id)

    clean_manager = EffectManager(FilesystemEffectStore(tmp_path / "clean-effects"))
    clean_request, clean_execution = file_execution(
        "write_file", request_id="request-clean"
    )
    await clean_manager.register_pending(clean_request, clean_execution)
    cleaned = await clean_manager.mark_cleaned(clean_request.request_id)

    assert rejected.status is EffectStatus.REJECTED
    assert cleaned is not None and cleaned.status is EffectStatus.CLEANED


@pytest.mark.asyncio
async def test_filesystem_effect_requires_checkpoint(tmp_path: Path) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))
    tool_request, execution = file_execution("write_file")
    execution = execution.model_copy(update={"checkpoint_id": None})

    with pytest.raises(Exception, match="checkpoint"):
        await manager.register_pending(tool_request, execution)


@pytest.mark.asyncio
async def test_non_filesystem_effect_rejects_checkpoint(tmp_path: Path) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))
    tool_request = request("memory_write")
    output = {"staged": True, "key": "project.preference"}
    execution = ToolExecutionResult(
        task_id=tool_request.task_id,
        step_id=tool_request.step_id,
        request_id=tool_request.request_id,
        checkpoint_id="unexpected-checkpoint",
        status=ExecutionStatus.PENDING_COMMIT,
        output=output,
        artifacts=[
            build_tool_output_artifact(
                tool_request,
                output,
                status=ExecutionStatus.PENDING_COMMIT,
            ),
            build_pending_memory_artifact(
                tool_request,
                memory_id="memory-1",
                key="project.preference",
                path="records/request-1/payload.json",
                content_sha256="c" * 64,
                size_bytes=10,
            ),
        ],
    )

    with pytest.raises(Exception, match="must not contain checkpoint"):
        await manager.register_pending(tool_request, execution)


@pytest.mark.asyncio
async def test_non_pending_execution_cannot_register_effect(tmp_path: Path) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))
    tool_request, execution = file_execution("write_file")
    execution = execution.model_copy(update={"status": ExecutionStatus.SUCCESS})

    with pytest.raises(Exception, match="PENDING_COMMIT"):
        await manager.register_pending(tool_request, execution)


@pytest.mark.asyncio
async def test_missing_effect_can_be_ignored_only_for_failure_cleanup(
    tmp_path: Path,
) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))

    assert await manager.mark_rolled_back("missing-request", missing_ok=True) is None
    assert await manager.mark_cleaned("missing-request", missing_ok=True) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("task_id", "other-task", "task_id"),
        ("step_id", "other-step", "step_id"),
        ("request_id", "other-request", "request_id"),
    ],
)
async def test_execution_correlation_mismatch_is_rejected(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))
    tool_request, execution = file_execution("write_file")
    execution = execution.model_copy(update={field: value})

    with pytest.raises(Exception, match=message):
        await manager.register_pending(tool_request, execution)


@pytest.mark.asyncio
async def test_invalid_artifacts_are_wrapped_as_effect_errors(tmp_path: Path) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))
    tool_request, execution = file_execution("write_file")
    artifacts = [dict(item) for item in execution.artifacts]
    artifacts[1]["sha256"] = "invalid"
    execution = execution.model_copy(update={"artifacts": artifacts})

    with pytest.raises(Exception, match="artifacts are invalid"):
        await manager.register_pending(tool_request, execution)


@pytest.mark.asyncio
async def test_unsupported_pending_tool_is_rejected(tmp_path: Path) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))
    tool_request = request("list_dir")
    output = {"entries": []}
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
            )
        ],
    )

    with pytest.raises(Exception, match="does not produce a managed effect"):
        await manager.register_pending(tool_request, execution)


@pytest.mark.asyncio
async def test_ensure_pending_validates_status_and_correlation(tmp_path: Path) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))
    tool_request, execution = file_execution("write_file")
    await manager.register_pending(tool_request, execution)

    with pytest.raises(Exception, match="checkpoint_id"):
        await manager.ensure_pending(tool_request.request_id, checkpoint_id="wrong")
    with pytest.raises(Exception, match="target_ref"):
        await manager.ensure_pending(
            tool_request.request_id, target_ref="file:wrong.txt"
        )
    with pytest.raises(Exception, match="kind"):
        await manager.ensure_pending(tool_request.request_id, kind="FILE_DELETE")

    await manager.mark_committed(tool_request.request_id, checkpoint_id="checkpoint-1")
    with pytest.raises(Exception, match="must be PENDING"):
        await manager.ensure_pending(tool_request.request_id)


@pytest.mark.asyncio
async def test_required_missing_effect_operations_fail_closed(tmp_path: Path) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))

    with pytest.raises(Exception, match="not found"):
        await manager.mark_committed("missing-request")
    with pytest.raises(Exception, match="not found"):
        await manager.mark_rolled_back("missing-request")
    with pytest.raises(Exception, match="not found"):
        await manager.mark_cleaned("missing-request")


@pytest.mark.asyncio
async def test_memory_target_rejects_control_characters(tmp_path: Path) -> None:
    manager = EffectManager(FilesystemEffectStore(tmp_path / "effects"))
    tool_request = request("memory_write")
    output = {"staged": True, "key": "project\nsecret"}
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
            build_pending_memory_artifact(
                tool_request,
                memory_id="memory-1",
                key="project\nsecret",
                path="records/request-1/payload.json",
                content_sha256="d" * 64,
                size_bytes=10,
            ),
        ],
    )

    with pytest.raises(Exception, match="control characters"):
        await manager.register_pending(tool_request, execution)
