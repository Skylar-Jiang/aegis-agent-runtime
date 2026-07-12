from datetime import UTC, datetime, timedelta

import pytest

from ra_agent.contracts import (
    ApprovalRequest,
    ApprovalStatus,
    ExecutionStatus,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.core.bootstrap import build_mock_container
from ra_agent.execution import (
    MockCheckpointManager,
    MockCommitGate,
    MockRollbackManager,
)
from ra_agent.security import MockApprovalService, MockDeepSafetyChecker
from ra_agent.tools import MockToolHandler, ToolRegistry


def make_request(tool_name: str = "write_file") -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id="request-1",
        tool_name=tool_name,
        arguments={"path": "draft.txt"},
        objective="update draft",
        context_summary="write is pending",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_safe_execution_mocks_return_correlated_no_side_effect_results() -> None:
    request = make_request()
    checkpoint = await MockCheckpointManager().create(request)
    pending = ToolExecutionResult(
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        checkpoint_id=checkpoint.checkpoint_id,
        status=ExecutionStatus.PENDING_COMMIT,
    )
    deep_check = await MockDeepSafetyChecker(passed=True).check(request, pending)
    committed = await MockCommitGate().commit(pending, deep_check)
    rolled_back = await MockRollbackManager().rollback(
        checkpoint.checkpoint_id, request.request_id
    )

    assert checkpoint.status is ExecutionStatus.SUCCESS
    assert committed.status is ExecutionStatus.COMMITTED
    assert committed.checkpoint_id == checkpoint.checkpoint_id
    assert rolled_back.status is ExecutionStatus.ROLLED_BACK
    assert rolled_back.checkpoint_id == checkpoint.checkpoint_id


@pytest.mark.asyncio
async def test_mock_approval_service_supports_decision_and_one_time_consumption() -> (
    None
):
    request = make_request("delete_file")
    approval = ApprovalRequest(
        approval_id="approval-1",
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        tool_name=request.tool_name,
        request_fingerprint="fingerprint-1",
        reason="review required",
        requested_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    service = MockApprovalService()
    await service.create(approval)
    decision = await service.grant(approval.approval_id, "reviewer", "approved")

    assert decision.status is ApprovalStatus.GRANTED
    assert await service.consume(approval.approval_id) == decision
    with pytest.raises(ValueError, match="already consumed"):
        await service.consume(approval.approval_id)


@pytest.mark.asyncio
async def test_mock_approval_service_expires_overdue_request_on_read() -> None:
    request = make_request("delete_file")
    now = datetime.now(UTC)
    approval = ApprovalRequest(
        approval_id="approval-expired",
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        tool_name=request.tool_name,
        request_fingerprint="fingerprint-expired",
        reason="review required",
        requested_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(minutes=1),
    )
    service = MockApprovalService()
    await service.create(approval)

    decision = await service.get_decision(approval.approval_id)

    assert decision is not None
    assert decision.status is ApprovalStatus.EXPIRED


@pytest.mark.asyncio
async def test_mock_approval_service_cannot_grant_after_expiry() -> None:
    request = make_request("delete_file")
    now = datetime.now(UTC)
    approval = ApprovalRequest(
        approval_id="approval-expired-grant",
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        tool_name=request.tool_name,
        request_fingerprint="fingerprint-expired-grant",
        reason="review required",
        requested_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(minutes=1),
    )
    service = MockApprovalService()
    await service.create(approval)

    with pytest.raises(ValueError, match="already decided"):
        await service.grant(approval.approval_id, "reviewer", "too late")

    decision = await service.get_decision(approval.approval_id)
    assert decision is not None
    assert decision.status is ApprovalStatus.EXPIRED


@pytest.mark.asyncio
async def test_tool_registry_keeps_specs_and_optional_handlers_separate() -> None:
    container = build_mock_container()
    source_spec = container.tool_registry.get_spec("list_dir")
    registry = ToolRegistry()
    handler = MockToolHandler()
    registry.register(source_spec, handler)

    assert registry.contains("list_dir")
    assert registry.get("list_dir") is source_spec
    assert registry.get_spec("list_dir") is source_spec
    assert registry.get_handler("list_dir") is handler
    assert registry.list_specs() == (source_spec,)
    assert registry.names() == ("list_dir",)
    result = await registry.get_handler("list_dir")(make_request("list_dir"))
    assert result.status is ExecutionStatus.SUCCESS

    with pytest.raises(ValueError, match="already registered"):
        registry.register(source_spec)


def test_tool_registry_reports_missing_tool_and_handler_clearly() -> None:
    container = build_mock_container()
    registry = ToolRegistry()
    registry.register(container.tool_registry.get_spec("list_dir"))

    with pytest.raises(KeyError, match="Unknown tool"):
        registry.get_spec("missing")
    with pytest.raises(LookupError, match="has no handler"):
        registry.get_handler("list_dir")


def test_mock_container_exposes_every_replaceable_runtime_boundary() -> None:
    container = build_mock_container()

    assert container.checkpoint_manager is not None
    assert container.commit_gate is not None
    assert container.rollback_manager is not None
    assert container.approval_service is not None
    assert container.deep_safety_checker is not None
    assert all(
        container.tool_registry.get_handler(name)
        for name in container.tool_registry.names()
    )
