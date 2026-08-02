import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from ra_agent.contracts import (
    ApprovalDecision,
    ApprovalStatus,
    ExecutionStatus,
    PermissionCheckResult,
    PermissionDecision,
    PermissionStatus,
    PolicyDecision,
    SourceType,
    ToolCallRequest,
)
from ra_agent.core.bootstrap import build_mock_container, build_runtime_scheduler
from ra_agent.execution import MockToolExecutor
from ra_agent.security import MockApprovalService


class CountingExecutor(MockToolExecutor):
    def __init__(self) -> None:
        self.calls = 0
        self.checkpoint_ids: list[str | None] = []
        self.approval_decisions: list[ApprovalDecision | None] = []

    async def execute(
        self,
        request: ToolCallRequest,
        *,
        checkpoint_id: str | None = None,
        approval_decision: ApprovalDecision | None = None,
    ):
        self.calls += 1
        self.checkpoint_ids.append(checkpoint_id)
        self.approval_decisions.append(approval_decision)
        return await super().execute(request, checkpoint_id=checkpoint_id)


class MismatchedApprovalService(MockApprovalService):
    async def create(self, approval):
        await super().create(approval)
        return approval.model_copy(update={"tool_name": "wrong-tool"})


class PendingConsumeApprovalService(MockApprovalService):
    async def consume(self, approval_id: str) -> ApprovalDecision:
        approval = await self.get_request(approval_id)
        return ApprovalDecision(
            approval_id=approval.approval_id,
            task_id=approval.task_id,
            step_id=approval.step_id,
            request_id=approval.request_id,
            status=ApprovalStatus.PENDING,
            decided_by="system",
            decided_at=datetime.now(UTC),
            reason="still pending",
        )


class StricterPolicy:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, verdict) -> PolicyDecision:
        self.calls += 1
        return (
            PolicyDecision.REQUEST_APPROVAL
            if self.calls == 1
            else PolicyDecision.SANDBOX_CHECK
        )


class MismatchedPermissionGate:
    async def check(self, request, tool_spec) -> PermissionCheckResult:
        return PermissionCheckResult(
            request_id="wrong-request",
            decisions=[
                PermissionDecision(
                    request_id=request.request_id,
                    permission=tool_spec.required_permissions[0],
                    status=PermissionStatus.GRANTED,
                    reason="mock",
                )
            ],
            allowed=True,
            requires_approval=False,
            reason="mismatched result",
        )


def make_request(
    tool_name: str = "delete_file", *, request_id: str = "request-approval"
) -> ToolCallRequest:
    arguments = {"path": "draft.txt"}
    if tool_name == "read_file":
        arguments = {"path": ".env"}
    if tool_name == "run_shell":
        arguments = {"command": "echo safe mock"}
    return ToolCallRequest(
        task_id="task-approval",
        step_id="step-approval",
        request_id=request_id,
        tool_name=tool_name,
        arguments=arguments,
        objective="perform reviewed operation",
        context_summary="operation requires explicit approval",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
    )


def make_scheduler():
    executor = CountingExecutor()
    container = replace(build_mock_container(), tool_executor=executor)
    return build_runtime_scheduler(container), container, executor


@pytest.mark.asyncio
async def test_first_schedule_waits_without_execution_and_repeat_reuses_approval() -> (
    None
):
    scheduler, container, executor = make_scheduler()
    request = make_request()

    first = await scheduler.schedule(request)
    repeated = await scheduler.schedule(
        request.model_copy(update={"requested_at": datetime.now(UTC)})
    )

    assert first.status is ExecutionStatus.WAITING_APPROVAL
    assert repeated == first
    assert executor.calls == 0
    approval_id = first.output["approval_id"]
    approval = await container.approval_service.get_request(approval_id)
    assert approval.status is ApprovalStatus.PENDING
    assert approval.request_id == request.request_id


@pytest.mark.asyncio
async def test_grant_resumes_reversible_high_risk_tool_through_sandbox() -> None:
    scheduler, container, executor = make_scheduler()
    request = make_request("delete_file")
    waiting = await scheduler.schedule(request)
    approval_id = waiting.output["approval_id"]
    decision = await container.approval_service.grant(
        approval_id, "reviewer", "approved"
    )

    result = await scheduler.resume_after_approval(request, approval_id)

    assert result.status is ExecutionStatus.COMMITTED
    assert executor.calls == 1
    assert executor.checkpoint_ids == [f"checkpoint-{request.request_id}"]
    assert executor.approval_decisions == [decision]


@pytest.mark.asyncio
async def test_grant_can_resume_approved_read_through_fast_path() -> None:
    scheduler, container, executor = make_scheduler()
    request = make_request("read_file")
    waiting = await scheduler.schedule(request)
    approval_id = waiting.output["approval_id"]
    decision = await container.approval_service.grant(
        approval_id, "reviewer", "approved"
    )

    result = await scheduler.resume_after_approval(request, approval_id)

    assert result.status is ExecutionStatus.COMMITTED
    assert executor.checkpoint_ids == [None]
    assert executor.approval_decisions == [decision]


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [ApprovalStatus.DENIED, ApprovalStatus.EXPIRED])
async def test_denied_or_expired_approval_blocks_without_execution(
    decision: ApprovalStatus,
) -> None:
    scheduler, container, executor = make_scheduler()
    request = make_request()
    waiting = await scheduler.schedule(request)
    approval_id = waiting.output["approval_id"]
    if decision is ApprovalStatus.DENIED:
        await container.approval_service.deny(approval_id, "reviewer", "denied")
    else:
        await container.approval_service.expire(approval_id)

    result = await scheduler.resume_after_approval(request, approval_id)

    assert result.status is ExecutionStatus.BLOCKED
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_changed_semantics_or_wrong_approval_id_cannot_resume() -> None:
    scheduler, container, executor = make_scheduler()
    request = make_request()
    waiting = await scheduler.schedule(request)
    approval_id = waiting.output["approval_id"]
    await container.approval_service.grant(approval_id, "reviewer", "approved")
    changed = request.model_copy(update={"arguments": {"path": "other.txt"}})

    changed_result = await scheduler.resume_after_approval(changed, approval_id)
    wrong_id_result = await scheduler.resume_after_approval(request, "approval-missing")

    assert changed_result.status is ExecutionStatus.FAILED
    assert changed_result.error_code == "APPROVAL_MISMATCH"
    assert wrong_id_result.status is ExecutionStatus.FAILED
    assert wrong_id_result.error_code == "APPROVAL_MISMATCH"
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_concurrent_resume_consumes_approval_once_and_reuses_final_result() -> (
    None
):
    scheduler, container, executor = make_scheduler()
    request = make_request("delete_file")
    waiting = await scheduler.schedule(request)
    approval_id = waiting.output["approval_id"]
    await container.approval_service.grant(approval_id, "reviewer", "approved")

    results = await asyncio.gather(
        *(scheduler.resume_after_approval(request, approval_id) for _ in range(20))
    )

    assert all(result.status is ExecutionStatus.COMMITTED for result in results)
    assert executor.calls == 1


@pytest.mark.asyncio
async def test_approval_never_enables_non_reversible_mock_execution() -> None:
    scheduler, container, executor = make_scheduler()
    request = make_request("run_shell")
    waiting = await scheduler.schedule(request)
    approval_id = waiting.output["approval_id"]
    await container.approval_service.grant(approval_id, "reviewer", "approved")

    result = await scheduler.resume_after_approval(request, approval_id)

    assert result.status is ExecutionStatus.BLOCKED
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_mismatched_created_approval_fails_structurally() -> None:
    executor = CountingExecutor()
    container = replace(
        build_mock_container(),
        tool_executor=executor,
        approval_service=MismatchedApprovalService(),
    )
    scheduler = build_runtime_scheduler(container)

    result = await scheduler.schedule(make_request())

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code == "CORRELATION_MISMATCH"
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_pending_consumed_decision_never_executes() -> None:
    executor = CountingExecutor()
    service = PendingConsumeApprovalService()
    container = replace(
        build_mock_container(), tool_executor=executor, approval_service=service
    )
    scheduler = build_runtime_scheduler(container)
    request = make_request()
    waiting = await scheduler.schedule(request)
    approval_id = waiting.output["approval_id"]
    await service.grant(approval_id, "reviewer", "approved before faulty consume")

    result = await scheduler.resume_after_approval(request, approval_id)

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code == "APPROVAL_NOT_GRANTED"
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_stricter_policy_after_approval_forces_sandbox() -> None:
    executor = CountingExecutor()
    policy = StricterPolicy()
    container = replace(
        build_mock_container(), tool_executor=executor, policy_engine=policy
    )
    scheduler = build_runtime_scheduler(container)
    request = make_request("read_file")
    waiting = await scheduler.schedule(request)
    approval_id = waiting.output["approval_id"]
    decision = await container.approval_service.grant(
        approval_id, "reviewer", "approved"
    )

    result = await scheduler.resume_after_approval(request, approval_id)

    assert result.status is ExecutionStatus.COMMITTED
    assert executor.checkpoint_ids == [f"checkpoint-{request.request_id}"]
    assert executor.approval_decisions == [decision]


@pytest.mark.asyncio
async def test_approval_permission_correlation_error_is_failed_not_blocked() -> None:
    executor = CountingExecutor()
    container = replace(
        build_mock_container(),
        tool_executor=executor,
        permission_gate=MismatchedPermissionGate(),
    )
    scheduler = build_runtime_scheduler(container)
    request = make_request("read_file")
    waiting = await scheduler.schedule(request)
    approval_id = waiting.output["approval_id"]
    await container.approval_service.grant(approval_id, "reviewer", "approved")

    result = await scheduler.resume_after_approval(request, approval_id)

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code == "CORRELATION_MISMATCH"
    assert executor.calls == 0
