import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ra_agent.audit import InMemoryAuditRecorder
from ra_agent.contracts import (
    ApprovalDecision,
    AuditEventType,
    ExecutionStatus,
    PermissionCheckResult,
    PermissionDecision,
    PermissionStatus,
    PermissionType,
    PolicyDecision,
    RecoverabilityType,
    RiskLevel,
    RiskVerdict,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
    ToolSpec,
)
from ra_agent.runtime.idempotency import InMemoryRequestExecutionRegistry
from ra_agent.runtime.scheduler import RuntimeScheduler
from ra_agent.security import MockPolicyEngine
from ra_agent.tools import ToolRegistry


class StaticClassifier:
    def __init__(self, risk_level: RiskLevel, decision: PolicyDecision) -> None:
        self.risk_level = risk_level
        self.decision = decision

    async def classify(self, request: ToolCallRequest) -> RiskVerdict:
        return RiskVerdict(
            request_id=request.request_id,
            risk_level=self.risk_level,
            recommended_decision=self.decision,
            reason=f"test verdict: {self.decision}",
        )


class TrackingPermissionGate:
    def __init__(self) -> None:
        self.calls = 0

    async def check(
        self, request: ToolCallRequest, tool_spec: ToolSpec
    ) -> PermissionCheckResult:
        self.calls += 1
        decisions = [
            PermissionDecision(
                request_id=request.request_id,
                permission=permission,
                status=PermissionStatus.NOT_REQUIRED,
                reason="test permission",
            )
            for permission in tool_spec.required_permissions
        ]
        return PermissionCheckResult(
            request_id=request.request_id,
            decisions=decisions,
            allowed=True,
            requires_approval=False,
            reason="test permissions allow execution",
        )


class OmittingPermissionGate(TrackingPermissionGate):
    async def check(
        self, request: ToolCallRequest, tool_spec: ToolSpec
    ) -> PermissionCheckResult:
        self.calls += 1
        return PermissionCheckResult(
            request_id=request.request_id,
            decisions=[],
            allowed=True,
            requires_approval=False,
            reason="incorrectly omitted required permission",
        )


class FailingPermissionGate(TrackingPermissionGate):
    async def check(
        self, request: ToolCallRequest, tool_spec: ToolSpec
    ) -> PermissionCheckResult:
        self.calls += 1
        raise RuntimeError("permission service failed")


class StaticPermissionGate(TrackingPermissionGate):
    def __init__(
        self,
        *,
        statuses: list[PermissionStatus],
        permissions: list[PermissionType] | None = None,
        result_request_id: str = "request-1",
        decision_request_ids: list[str] | None = None,
        allowed: bool = True,
        requires_approval: bool = False,
    ) -> None:
        super().__init__()
        self.statuses = statuses
        self.permissions = permissions
        self.result_request_id = result_request_id
        self.decision_request_ids = decision_request_ids
        self.allowed = allowed
        self.requires_approval = requires_approval

    async def check(
        self, request: ToolCallRequest, tool_spec: ToolSpec
    ) -> PermissionCheckResult:
        self.calls += 1
        permissions = self.permissions or tool_spec.required_permissions
        request_ids = self.decision_request_ids or [request.request_id] * len(
            permissions
        )
        return PermissionCheckResult(
            request_id=self.result_request_id,
            decisions=[
                PermissionDecision(
                    request_id=request_id,
                    permission=permission,
                    status=status,
                    reason="matrix permission",
                )
                for request_id, permission, status in zip(
                    request_ids, permissions, self.statuses, strict=True
                )
            ],
            allowed=self.allowed,
            requires_approval=self.requires_approval,
            reason="matrix result",
        )


class TrackingExecutor:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls = 0
        self.raises = raises
        self.approval_decisions: list[ApprovalDecision | None] = []

    async def execute(
        self,
        request: ToolCallRequest,
        *,
        checkpoint_id: str | None = None,
        approval_decision: ApprovalDecision | None = None,
    ) -> ToolExecutionResult:
        self.calls += 1
        self.approval_decisions.append(approval_decision)
        if self.raises:
            raise RuntimeError("executor failed")
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.SUCCESS,
            output={"mock": True},
        )


class SlowTrackingExecutor(TrackingExecutor):
    async def execute(
        self,
        request: ToolCallRequest,
        *,
        checkpoint_id: str | None = None,
        approval_decision: ApprovalDecision | None = None,
    ) -> ToolExecutionResult:
        await asyncio.sleep(0.02)
        return await super().execute(request, approval_decision=approval_decision)


def make_request(
    *, task_id: str = "task-1", request_id: str = "request-1"
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id=task_id,
        step_id=f"step-{request_id}",
        request_id=request_id,
        tool_name="list_dir",
        arguments={"path": "."},
        objective="inspect workspace",
        context_summary="Need a directory listing.",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="list_dir",
            description="List a workspace directory",
            required_permissions=[PermissionType.FILE_LIST],
            base_risk=RiskLevel.LOW,
            side_effect_type="NONE",
            reversibility=RecoverabilityType.ATOMIC,
            sandbox_mode="NONE",
            timeout_seconds=5,
            network_required=False,
            supports_dry_run=True,
        )
    )
    return registry


def make_scheduler(
    decision: PolicyDecision,
    *,
    recorder: InMemoryAuditRecorder | None = None,
    executor: TrackingExecutor | None = None,
    permission_gate: TrackingPermissionGate | None = None,
) -> tuple[
    RuntimeScheduler, InMemoryAuditRecorder, TrackingExecutor, TrackingPermissionGate
]:
    risk = {
        PolicyDecision.FAST_EXECUTE: RiskLevel.LOW,
        PolicyDecision.SANDBOX_CHECK: RiskLevel.MEDIUM,
        PolicyDecision.REQUEST_APPROVAL: RiskLevel.HIGH,
        PolicyDecision.BLOCK: RiskLevel.CRITICAL,
    }[decision]
    actual_recorder = recorder or InMemoryAuditRecorder()
    actual_executor = executor or TrackingExecutor()
    actual_gate = permission_gate or TrackingPermissionGate()
    scheduler = RuntimeScheduler(
        classifier=StaticClassifier(risk, decision),
        policy=MockPolicyEngine(),
        permission_gate=actual_gate,
        executor=actual_executor,
        audit_recorder=actual_recorder,
        tool_registry=make_registry(),
        request_registry=InMemoryRequestExecutionRegistry(),
    )
    return scheduler, actual_recorder, actual_executor, actual_gate


@pytest.mark.asyncio
async def test_low_request_executes_once_commits_and_audits_full_path() -> None:
    scheduler, recorder, executor, permission_gate = make_scheduler(
        PolicyDecision.FAST_EXECUTE
    )

    result = await scheduler.schedule(make_request())

    assert executor.calls == 1
    assert executor.approval_decisions == [None]
    assert permission_gate.calls == 1
    assert result.status is ExecutionStatus.COMMITTED
    assert [event.event_type for event in recorder.events_for("task-1")] == [
        AuditEventType.TOOL_REQUESTED,
        AuditEventType.RISK_CLASSIFIED,
        AuditEventType.PERMISSION_CHECKED,
        AuditEventType.PRE_CHECK_STARTED,
        AuditEventType.PRE_CHECK_FINISHED,
        AuditEventType.EXECUTION_STARTED,
        AuditEventType.POST_CHECK_STARTED,
        AuditEventType.POST_CHECK_FINISHED,
        AuditEventType.EXECUTION_FINISHED,
    ]


@pytest.mark.asyncio
async def test_block_request_skips_permissions_and_executor() -> None:
    scheduler, recorder, executor, permission_gate = make_scheduler(
        PolicyDecision.BLOCK
    )

    result = await scheduler.schedule(make_request())

    assert executor.calls == 0
    assert permission_gate.calls == 0
    assert result.status is ExecutionStatus.BLOCKED
    assert "test verdict" in (result.error or "")
    assert [event.event_type for event in recorder.events_for("task-1")] == [
        AuditEventType.TOOL_REQUESTED,
        AuditEventType.RISK_CLASSIFIED,
        AuditEventType.TOOL_BLOCKED,
    ]
    assert recorder.events_for("task-1")[-1].details["reason"] == result.error


@pytest.mark.asyncio
async def test_audit_sequences_continue_across_requests_and_restart_per_task() -> None:
    recorder = InMemoryAuditRecorder()
    scheduler, _, _, _ = make_scheduler(PolicyDecision.FAST_EXECUTE, recorder=recorder)

    await scheduler.schedule(make_request(request_id="request-1"))
    await scheduler.schedule(make_request(request_id="request-2"))
    other_scheduler, _, _, _ = make_scheduler(
        PolicyDecision.FAST_EXECUTE, recorder=recorder
    )
    await other_scheduler.schedule(
        make_request(task_id="task-2", request_id="request-3")
    )

    task_one_sequences = [
        event.sequence_number for event in recorder.events_for("task-1")
    ]
    task_two_sequences = [
        event.sequence_number for event in recorder.events_for("task-2")
    ]
    assert task_one_sequences == list(range(1, 19))
    assert task_two_sequences == list(range(1, 10))


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [PolicyDecision.REQUEST_APPROVAL])
async def test_unconfigured_policy_dependency_returns_failure_without_execution(
    decision: PolicyDecision,
) -> None:
    scheduler, recorder, executor, _ = make_scheduler(decision)

    result = await scheduler.schedule(make_request())

    assert executor.calls == 0
    assert result.status is ExecutionStatus.FAILED
    assert "dependencies are not configured" in (result.error or "")
    assert recorder.events_for("task-1")[-1].event_type is AuditEventType.STEP_FAILED


@pytest.mark.asyncio
async def test_executor_exception_is_structured_and_audited() -> None:
    executor = TrackingExecutor(raises=True)
    scheduler, recorder, _, _ = make_scheduler(
        PolicyDecision.FAST_EXECUTE, executor=executor
    )

    result = await scheduler.schedule(make_request())

    assert executor.calls == 1
    assert result.status is ExecutionStatus.FAILED
    assert result.error == "executor failed"
    assert [event.event_type for event in recorder.events_for("task-1")[-2:]] == [
        AuditEventType.EXECUTION_FINISHED,
        AuditEventType.STEP_FAILED,
    ]


@pytest.mark.asyncio
async def test_missing_required_permission_decision_blocks_execution() -> None:
    executor = TrackingExecutor()
    scheduler, recorder, _, _ = make_scheduler(
        PolicyDecision.FAST_EXECUTE,
        executor=executor,
        permission_gate=OmittingPermissionGate(),
    )

    result = await scheduler.schedule(make_request())

    assert executor.calls == 0
    assert result.status is ExecutionStatus.BLOCKED
    assert "missing permission decisions" in (result.error or "")
    assert recorder.events_for("task-1")[-1].event_type is AuditEventType.TOOL_BLOCKED


@pytest.mark.asyncio
async def test_permission_gate_exception_is_structured_and_audited() -> None:
    executor = TrackingExecutor()
    scheduler, recorder, _, _ = make_scheduler(
        PolicyDecision.FAST_EXECUTE,
        executor=executor,
        permission_gate=FailingPermissionGate(),
    )

    result = await scheduler.schedule(make_request())

    assert executor.calls == 0
    assert result.status is ExecutionStatus.FAILED
    assert result.error == "permission service failed"
    assert recorder.events_for("task-1")[-1].event_type is AuditEventType.STEP_FAILED


@pytest.mark.asyncio
async def test_serial_duplicate_request_executes_once_and_returns_first_result() -> (
    None
):
    scheduler, _, executor, _ = make_scheduler(PolicyDecision.FAST_EXECUTE)
    request = make_request()

    first = await scheduler.schedule(request)
    second = await scheduler.schedule(request)

    assert executor.calls == 1
    assert second == first


@pytest.mark.asyncio
async def test_concurrent_duplicate_request_waits_and_executes_once() -> None:
    executor = SlowTrackingExecutor()
    scheduler, _, _, _ = make_scheduler(PolicyDecision.FAST_EXECUTE, executor=executor)
    request = make_request()

    results = await asyncio.gather(*(scheduler.schedule(request) for _ in range(20)))

    assert executor.calls == 1
    assert all(result == results[0] for result in results)
    assert results[0].status is ExecutionStatus.COMMITTED


@pytest.mark.asyncio
async def test_same_request_id_with_different_semantics_returns_conflict() -> None:
    scheduler, recorder, executor, _ = make_scheduler(PolicyDecision.FAST_EXECUTE)
    first_request = make_request()
    conflicting_request = first_request.model_copy(
        update={"arguments": {"path": "different"}}
    )

    first = await scheduler.schedule(first_request)
    conflict = await scheduler.schedule(conflicting_request)

    assert first.status is ExecutionStatus.COMMITTED
    assert conflict.status is ExecutionStatus.FAILED
    assert conflict.error_code == "REQUEST_ID_CONFLICT"
    assert executor.calls == 1
    assert recorder.events_for("task-1")[-1].event_type is AuditEventType.STEP_FAILED
    assert (
        recorder.events_for("task-1")[-1].details["error_code"] == "REQUEST_ID_CONFLICT"
    )


@pytest.mark.asyncio
async def test_different_request_ids_execute_independently() -> None:
    scheduler, _, executor, _ = make_scheduler(PolicyDecision.FAST_EXECUTE)

    first = await scheduler.schedule(make_request(request_id="request-1"))
    second = await scheduler.schedule(make_request(request_id="request-2"))

    assert executor.calls == 2
    assert first.status is ExecutionStatus.COMMITTED
    assert second.status is ExecutionStatus.COMMITTED


@pytest.mark.asyncio
async def test_failed_execution_is_cached_without_repeating_side_effect() -> None:
    executor = TrackingExecutor(raises=True)
    scheduler, _, _, _ = make_scheduler(PolicyDecision.FAST_EXECUTE, executor=executor)
    request = make_request()

    first = await scheduler.schedule(request)
    second = await scheduler.schedule(request)

    assert executor.calls == 1
    assert first.status is ExecutionStatus.FAILED
    assert second == first


@pytest.mark.asyncio
async def test_requested_at_change_does_not_create_request_id_conflict() -> None:
    scheduler, _, executor, _ = make_scheduler(PolicyDecision.FAST_EXECUTE)
    first_request = make_request()
    retry = first_request.model_copy(
        update={"requested_at": first_request.requested_at + timedelta(seconds=30)}
    )

    first = await scheduler.schedule(first_request)
    second = await scheduler.schedule(retry)

    assert executor.calls == 1
    assert second == first


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [PermissionStatus.GRANTED, PermissionStatus.NOT_REQUIRED]
)
async def test_permission_matrix_allows_only_positive_statuses(
    status: PermissionStatus,
) -> None:
    gate = StaticPermissionGate(statuses=[status])
    scheduler, _, executor, _ = make_scheduler(
        PolicyDecision.FAST_EXECUTE, permission_gate=gate
    )

    result = await scheduler.schedule(make_request())

    assert executor.calls == 1
    assert result.status is ExecutionStatus.COMMITTED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [PermissionStatus.DENIED, PermissionStatus.PENDING, PermissionStatus.EXPIRED],
)
async def test_permission_matrix_blocks_non_executable_statuses(
    status: PermissionStatus,
) -> None:
    gate = StaticPermissionGate(statuses=[status], allowed=False)
    scheduler, recorder, executor, _ = make_scheduler(
        PolicyDecision.FAST_EXECUTE, permission_gate=gate
    )

    result = await scheduler.schedule(make_request())

    assert executor.calls == 0
    assert result.status is ExecutionStatus.BLOCKED
    assert recorder.events_for("task-1")[-1].event_type is AuditEventType.TOOL_BLOCKED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gate",
    [
        StaticPermissionGate(
            statuses=[PermissionStatus.GRANTED, PermissionStatus.GRANTED],
            permissions=[PermissionType.FILE_LIST, PermissionType.SENSITIVE_READ],
        ),
        StaticPermissionGate(
            statuses=[PermissionStatus.GRANTED], requires_approval=True
        ),
        StaticPermissionGate(statuses=[PermissionStatus.GRANTED], allowed=False),
        StaticPermissionGate(statuses=[PermissionStatus.DENIED], allowed=True),
        StaticPermissionGate(
            statuses=[PermissionStatus.GRANTED, PermissionStatus.GRANTED],
            permissions=[PermissionType.FILE_LIST, PermissionType.FILE_LIST],
        ),
    ],
    ids=[
        "extra-permission",
        "requires-approval",
        "allowed-contradiction",
        "denied-allowed-contradiction",
        "duplicate-permission",
    ],
)
async def test_permission_matrix_blocks_inconsistent_results(
    gate: StaticPermissionGate,
) -> None:
    scheduler, recorder, executor, _ = make_scheduler(
        PolicyDecision.FAST_EXECUTE, permission_gate=gate
    )

    result = await scheduler.schedule(make_request())

    assert executor.calls == 0
    assert result.status is ExecutionStatus.BLOCKED
    assert recorder.events_for("task-1")[-1].event_type is AuditEventType.TOOL_BLOCKED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "gate",
    [
        StaticPermissionGate(
            statuses=[PermissionStatus.GRANTED], result_request_id="other-request"
        ),
        StaticPermissionGate(
            statuses=[PermissionStatus.GRANTED],
            decision_request_ids=["other-request"],
        ),
    ],
    ids=["result-request-id", "decision-request-id"],
)
async def test_permission_correlation_mismatch_fails_structurally(
    gate: StaticPermissionGate,
) -> None:
    scheduler, recorder, executor, _ = make_scheduler(
        PolicyDecision.FAST_EXECUTE, permission_gate=gate
    )

    result = await scheduler.schedule(make_request())

    assert executor.calls == 0
    assert result.status is ExecutionStatus.FAILED
    assert result.error_code == "CORRELATION_MISMATCH"
    assert recorder.events_for("task-1")[-1].event_type is AuditEventType.STEP_FAILED
