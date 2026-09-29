from datetime import UTC, datetime

import pytest
from ra_agent.contracts import (
    ExecutionStatus,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.runtime.idempotency import InMemoryRequestExecutionRegistry


def make_request() -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id="request-1",
        tool_name="delete_file",
        arguments={"path": "draft.txt"},
        objective="remove draft",
        context_summary="user requested deletion",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
    )


def make_result(status: ExecutionStatus) -> ToolExecutionResult:
    request = make_request()
    return ToolExecutionResult(
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        status=status,
    )


@pytest.mark.asyncio
async def test_waiting_result_can_be_atomically_resumed_to_one_final_result() -> None:
    registry = InMemoryRequestExecutionRegistry()
    request = make_request()
    initial = await registry.claim(request)
    await registry.complete(initial, make_result(ExecutionStatus.WAITING_APPROVAL))

    duplicate = await registry.claim(request)
    assert await registry.wait(duplicate) == make_result(
        ExecutionStatus.WAITING_APPROVAL
    )

    resume_owner = await registry.claim_resume(request)
    duplicate_resume = await registry.claim_resume(request)
    assert resume_owner.owns_execution is True
    assert duplicate_resume.owns_execution is False

    final = make_result(ExecutionStatus.COMMITTED)
    await registry.complete(resume_owner, final)
    assert await registry.wait(duplicate_resume) == final
    assert await registry.wait(await registry.claim(request)) == final


@pytest.mark.asyncio
async def test_terminal_result_cannot_be_reopened_for_resume() -> None:
    registry = InMemoryRequestExecutionRegistry()
    request = make_request()
    claim = await registry.claim(request)
    final = make_result(ExecutionStatus.BLOCKED)
    await registry.complete(claim, final)

    resumed = await registry.claim_resume(request)

    assert resumed.owns_execution is False
    assert await registry.wait(resumed) == final
