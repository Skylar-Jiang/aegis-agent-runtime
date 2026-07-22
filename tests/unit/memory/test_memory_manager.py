from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ra_agent.contracts import (
    MemoryStatus,
    PostCheckResult,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.memory import (
    FilesystemMemoryStore,
    MemoryLifecycleCorrelationError,
    MemoryLifecycleIntegrityError,
    MemoryLifecycleManager,
    MemoryLifecyclePreconditionError,
)
from ra_agent.tools.implementations.memory_tools import MemoryWriteHandler


def make_request(request_id: str = "request-manager") -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id=request_id,
        tool_name="memory_write",
        arguments={"key": "project.note", "value": {"text": "candidate"}},
        objective="stage memory for post check",
        context_summary="memory lifecycle manager unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def post_check(request_id: str, *, passed: bool) -> PostCheckResult:
    return PostCheckResult(
        request_id=request_id,
        passed=passed,
        reason="safe" if passed else "prompt injection detected",
        signals=[] if passed else ["PROMPT_INJECTION"],
    )


@pytest.fixture
def store(tmp_path: Path) -> FilesystemMemoryStore:
    return FilesystemMemoryStore(tmp_path / "memory", max_value_bytes=4096)


@pytest.fixture
def manager(store: FilesystemMemoryStore) -> MemoryLifecycleManager:
    return MemoryLifecycleManager(store)


async def prepare_execution(
    store: FilesystemMemoryStore,
    request: ToolCallRequest,
) -> ToolExecutionResult:
    return await MemoryWriteHandler(store)(request)


@pytest.mark.asyncio
async def test_passing_post_check_commits_pending_memory(
    store: FilesystemMemoryStore,
    manager: MemoryLifecycleManager,
) -> None:
    request = make_request("request-commit")
    execution = await prepare_execution(store, request)

    record = await manager.commit(
        request,
        execution,
        post_check(request.request_id, passed=True),
    )

    assert record.status is MemoryStatus.TRUSTED
    visible = await store.get_trusted_value("project.note")
    assert visible is not None
    assert visible[1] == {"text": "candidate"}


@pytest.mark.asyncio
async def test_failing_post_check_rejects_pending_memory(
    store: FilesystemMemoryStore,
    manager: MemoryLifecycleManager,
) -> None:
    request = make_request("request-reject")
    execution = await prepare_execution(store, request)

    record = await manager.reject(
        request,
        execution,
        post_check(request.request_id, passed=False),
    )

    assert record.status is MemoryStatus.REJECTED
    assert record.rejected_reason == "prompt injection detected"
    assert await store.get_trusted("project.note") is None


@pytest.mark.asyncio
async def test_commit_requires_passing_post_check(
    store: FilesystemMemoryStore,
    manager: MemoryLifecycleManager,
) -> None:
    request = make_request("request-commit-failed-check")
    execution = await prepare_execution(store, request)

    with pytest.raises(MemoryLifecyclePreconditionError):
        await manager.commit(
            request,
            execution,
            post_check(request.request_id, passed=False),
        )

    assert (await store.get(request.request_id)).status is MemoryStatus.PENDING


@pytest.mark.asyncio
async def test_reject_requires_failing_post_check(
    store: FilesystemMemoryStore,
    manager: MemoryLifecycleManager,
) -> None:
    request = make_request("request-reject-passed-check")
    execution = await prepare_execution(store, request)

    with pytest.raises(MemoryLifecyclePreconditionError):
        await manager.reject(
            request,
            execution,
            post_check(request.request_id, passed=True),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["execution", "post_check"])
async def test_request_id_mismatch_is_rejected(
    store: FilesystemMemoryStore,
    manager: MemoryLifecycleManager,
    source: str,
) -> None:
    request = make_request(f"request-mismatch-{source}")
    execution = await prepare_execution(store, request)
    check = post_check(request.request_id, passed=True)

    if source == "execution":
        execution = execution.model_copy(update={"request_id": "other-request"})
    else:
        check = check.model_copy(update={"request_id": "other-request"})

    with pytest.raises(MemoryLifecycleCorrelationError):
        await manager.commit(request, execution, check)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("memory_id", "memory-other"),
        ("key", "other.key"),
        ("path", "records/other/payload.json"),
        ("sha256", "0" * 64),
        ("size_bytes", 999),
        ("status", "TRUSTED"),
    ],
)
async def test_artifact_mismatch_is_rejected(
    store: FilesystemMemoryStore,
    manager: MemoryLifecycleManager,
    field: str,
    replacement: object,
) -> None:
    request = make_request(f"request-artifact-{field}")
    execution = await prepare_execution(store, request)
    artifacts = [dict(artifact) for artifact in execution.artifacts]
    artifacts[1][field] = replacement
    execution = execution.model_copy(update={"artifacts": artifacts})

    with pytest.raises(MemoryLifecycleIntegrityError):
        await manager.commit(
            request,
            execution,
            post_check(request.request_id, passed=True),
        )


@pytest.mark.asyncio
async def test_missing_pending_memory_artifact_is_rejected(
    store: FilesystemMemoryStore,
    manager: MemoryLifecycleManager,
) -> None:
    request = make_request("request-missing-artifact")
    execution = await prepare_execution(store, request)
    execution = execution.model_copy(update={"artifacts": execution.artifacts[:1]})

    with pytest.raises(MemoryLifecycleIntegrityError):
        await manager.commit(
            request,
            execution,
            post_check(request.request_id, passed=True),
        )


@pytest.mark.asyncio
async def test_pending_change_mismatch_is_rejected(
    store: FilesystemMemoryStore,
    manager: MemoryLifecycleManager,
) -> None:
    request = make_request("request-change-mismatch")
    execution = await prepare_execution(store, request)
    change: dict[str, Any] = dict(execution.pending_changes[0])
    change["content_sha256"] = "f" * 64
    execution = execution.model_copy(update={"pending_changes": [change]})

    with pytest.raises(MemoryLifecycleIntegrityError):
        await manager.commit(
            request,
            execution,
            post_check(request.request_id, passed=True),
        )


@pytest.mark.asyncio
async def test_pending_and_trusted_memory_can_be_rolled_back(
    store: FilesystemMemoryStore,
    manager: MemoryLifecycleManager,
) -> None:
    pending_request = make_request("request-rollback-pending")
    await prepare_execution(store, pending_request)
    pending_rollback = await manager.rollback(
        pending_request.request_id,
        reason="runtime cancelled",
    )
    assert pending_rollback.status is MemoryStatus.ROLLED_BACK

    trusted_request = make_request("request-rollback-trusted")
    trusted_execution = await prepare_execution(store, trusted_request)
    await manager.commit(
        trusted_request,
        trusted_execution,
        post_check(trusted_request.request_id, passed=True),
    )
    trusted_rollback = await manager.rollback(
        trusted_request.request_id,
        reason="late poisoning signal",
    )
    assert trusted_rollback.status is MemoryStatus.ROLLED_BACK
    assert await store.get_trusted("project.note") is None


@pytest.mark.asyncio
async def test_commit_and_reject_retries_are_idempotent(
    store: FilesystemMemoryStore,
    manager: MemoryLifecycleManager,
) -> None:
    commit_request = make_request("request-manager-commit-retry")
    commit_execution = await prepare_execution(store, commit_request)
    first_commit = await manager.commit(
        commit_request,
        commit_execution,
        post_check(commit_request.request_id, passed=True),
    )
    second_commit = await manager.commit(
        commit_request,
        commit_execution,
        post_check(commit_request.request_id, passed=True),
    )
    assert first_commit == second_commit

    reject_request = make_request("request-manager-reject-retry")
    reject_execution = await prepare_execution(store, reject_request)
    first_reject = await manager.reject(
        reject_request,
        reject_execution,
        post_check(reject_request.request_id, passed=False),
    )
    second_reject = await manager.reject(
        reject_request,
        reject_execution,
        post_check(reject_request.request_id, passed=False),
    )
    assert first_reject == second_reject
