import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ra_agent.audit import InMemoryAuditRecorder
from ra_agent.contracts import (
    ApprovalDecision,
    AuditEventType,
    CheckpointResult,
    CommitResult,
    DeepCheckResult,
    ExecutionStatus,
    PostCheckResult,
    PreCheckResult,
    RollbackResult,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.core.bootstrap import build_mock_container, build_runtime_scheduler


def make_request() -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-sandbox",
        step_id="step-sandbox",
        request_id="request-sandbox",
        tool_name="write_file",
        arguments={"path": "draft.txt", "content": "safe mock"},
        objective="update draft",
        context_summary="write should remain pending until checked",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
    )


class SpyCheckpointManager:
    def __init__(self, *, status: ExecutionStatus = ExecutionStatus.SUCCESS) -> None:
        self.status = status
        self.calls = 0

    async def create(self, request: ToolCallRequest) -> CheckpointResult:
        self.calls += 1
        return CheckpointResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            checkpoint_id=f"checkpoint-{request.request_id}",
            status=self.status,
        )


class SpyExecutor:
    def __init__(
        self,
        *,
        status: ExecutionStatus = ExecutionStatus.PENDING_COMMIT,
        error: Exception | None = None,
        request_id: str | None = None,
        checkpoint_id: str | None = None,
    ) -> None:
        self.status = status
        self.error = error
        self.request_id = request_id
        self.checkpoint_id = checkpoint_id
        self.calls = 0

    async def execute(
        self,
        request: ToolCallRequest,
        *,
        checkpoint_id: str | None = None,
        approval_decision: ApprovalDecision | None = None,
    ) -> ToolExecutionResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=self.request_id or request.request_id,
            checkpoint_id=self.checkpoint_id or checkpoint_id,
            status=self.status,
            output={"mock": True},
            pending_changes=[{"path": "draft.txt"}],
        )


class SpyDeepChecker:
    def __init__(self, *, passed: bool = True, request_id: str | None = None) -> None:
        self.passed = passed
        self.request_id = request_id
        self.calls = 0

    async def check(
        self, request: ToolCallRequest, result: ToolExecutionResult
    ) -> DeepCheckResult:
        self.calls += 1
        return DeepCheckResult(
            request_id=self.request_id or request.request_id,
            passed=self.passed,
            reason="spy deep check",
        )


class SpyPreChecker:
    def __init__(self, *, passed: bool = True, error: Exception | None = None) -> None:
        self.passed = passed
        self.error = error
        self.calls = 0

    async def check(self, request: ToolCallRequest, verdict) -> PreCheckResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return PreCheckResult(
            request_id=request.request_id,
            passed=self.passed,
            reason="spy pre check",
        )


class SpyPostChecker:
    def __init__(self, *, passed: bool = True, error: Exception | None = None) -> None:
        self.passed = passed
        self.error = error
        self.calls = 0

    async def check(
        self, request: ToolCallRequest, result: ToolExecutionResult
    ) -> PostCheckResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return PostCheckResult(
            request_id=request.request_id,
            passed=self.passed,
            reason="spy post check",
        )


class SpyCommitGate:
    def __init__(
        self,
        *,
        status: ExecutionStatus = ExecutionStatus.COMMITTED,
        request_id: str | None = None,
        checkpoint_id: str | None = None,
    ) -> None:
        self.status = status
        self.request_id = request_id
        self.checkpoint_id = checkpoint_id
        self.calls = 0

    async def commit(
        self, execution: ToolExecutionResult, deep_check: DeepCheckResult
    ) -> CommitResult:
        self.calls += 1
        return CommitResult(
            request_id=self.request_id or execution.request_id,
            checkpoint_id=self.checkpoint_id or execution.checkpoint_id,
            status=self.status,
        )


class SpyRollbackManager:
    def __init__(
        self,
        *,
        request_id: str | None = None,
        checkpoint_id: str | None = None,
    ) -> None:
        self.request_id = request_id
        self.checkpoint_id = checkpoint_id
        self.calls = 0

    async def rollback(self, checkpoint_id: str, request_id: str) -> RollbackResult:
        self.calls += 1
        return RollbackResult(
            request_id=self.request_id or request_id,
            checkpoint_id=self.checkpoint_id or checkpoint_id,
            status=ExecutionStatus.ROLLED_BACK,
            reason="spy rollback",
        )


class CancellingExecutor(SpyExecutor):
    async def execute(
        self,
        request: ToolCallRequest,
        *,
        checkpoint_id: str | None = None,
        approval_decision: ApprovalDecision | None = None,
    ) -> ToolExecutionResult:
        self.calls += 1
        raise asyncio.CancelledError


class CancellingDeepChecker(SpyDeepChecker):
    async def check(
        self, request: ToolCallRequest, result: ToolExecutionResult
    ) -> DeepCheckResult:
        self.calls += 1
        raise asyncio.CancelledError


class CancellingCommitGate(SpyCommitGate):
    async def commit(
        self, execution: ToolExecutionResult, deep_check: DeepCheckResult
    ) -> CommitResult:
        self.calls += 1
        raise asyncio.CancelledError


class CancellingAuditRecorder(InMemoryAuditRecorder):
    def __init__(self, event_type: AuditEventType) -> None:
        super().__init__()
        self.event_type = event_type
        self.cancelled = False

    async def record(self, **kwargs):
        if kwargs["event_type"] is self.event_type and not self.cancelled:
            self.cancelled = True
            raise asyncio.CancelledError
        return await super().record(**kwargs)


def make_scheduler(
    *,
    checkpoint: object | None = None,
    executor: object | None = None,
    deep: object | None = None,
    pre: object | None = None,
    post: object | None = None,
    commit: object | None = None,
    rollback: object | None = None,
    recorder: object | None = None,
):
    container = build_mock_container()
    container = replace(
        container,
        checkpoint_manager=checkpoint or SpyCheckpointManager(),
        tool_executor=executor or SpyExecutor(),
        deep_safety_checker=deep or SpyDeepChecker(),
        pre_execution_checker=pre or SpyPreChecker(),
        post_execution_checker=post or SpyPostChecker(),
        commit_gate=commit or SpyCommitGate(),
        rollback_manager=rollback or SpyRollbackManager(),
        audit_recorder=recorder or container.audit_recorder,
    )
    return build_runtime_scheduler(container), container


@pytest.mark.asyncio
async def test_pre_check_rejection_prevents_checkpoint_and_execution() -> None:
    checkpoint = SpyCheckpointManager()
    executor = SpyExecutor()
    scheduler, _ = make_scheduler(
        pre=SpyPreChecker(passed=False), checkpoint=checkpoint, executor=executor
    )

    result = await scheduler.schedule(make_request())

    assert result.status is ExecutionStatus.BLOCKED
    assert checkpoint.calls == 0
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_post_check_failure_rolls_back_and_never_commits() -> None:
    commit = SpyCommitGate()
    rollback = SpyRollbackManager()
    scheduler, _ = make_scheduler(
        post=SpyPostChecker(error=RuntimeError("post checker unavailable")),
        commit=commit,
        rollback=rollback,
    )

    result = await scheduler.schedule(make_request())

    assert result.status is ExecutionStatus.ROLLED_BACK
    assert result.error_code == "POST_CHECK_FAILED"
    assert commit.calls == 0
    assert rollback.calls == 1


@pytest.mark.asyncio
async def test_sandbox_deep_check_passes_and_commits_once_in_audited_order() -> None:
    commit = SpyCommitGate()
    rollback = SpyRollbackManager()
    scheduler, container = make_scheduler(commit=commit, rollback=rollback)

    result = await scheduler.schedule(make_request())

    assert isinstance(container.audit_recorder, InMemoryAuditRecorder)
    assert result.status is ExecutionStatus.COMMITTED
    assert result.checkpoint_id == "checkpoint-request-sandbox"
    assert commit.calls == 1
    assert rollback.calls == 0
    assert [
        event.event_type
        for event in container.audit_recorder.events_for("task-sandbox")
    ] == [
        AuditEventType.TOOL_REQUESTED,
        AuditEventType.RISK_CLASSIFIED,
        AuditEventType.PERMISSION_CHECKED,
        AuditEventType.PRE_CHECK_STARTED,
        AuditEventType.PRE_CHECK_FINISHED,
        AuditEventType.CHECKPOINT_CREATED,
        AuditEventType.EXECUTION_STARTED,
        AuditEventType.EXECUTION_FINISHED,
        AuditEventType.POST_CHECK_STARTED,
        AuditEventType.POST_CHECK_FINISHED,
        AuditEventType.DEEP_CHECK_STARTED,
        AuditEventType.DEEP_CHECK_FINISHED,
        AuditEventType.COMMIT_STARTED,
        AuditEventType.COMMIT_FINISHED,
    ]


@pytest.mark.asyncio
async def test_failed_deep_check_rolls_back_once_and_never_commits() -> None:
    commit = SpyCommitGate()
    rollback = SpyRollbackManager()
    scheduler, _ = make_scheduler(
        deep=SpyDeepChecker(passed=False), commit=commit, rollback=rollback
    )

    result = await scheduler.schedule(make_request())

    assert result.status is ExecutionStatus.ROLLED_BACK
    assert commit.calls == 0
    assert rollback.calls == 1


@pytest.mark.asyncio
async def test_checkpoint_failure_stops_before_executor() -> None:
    executor = SpyExecutor()
    scheduler, _ = make_scheduler(
        checkpoint=SpyCheckpointManager(status=ExecutionStatus.FAILED),
        executor=executor,
    )

    result = await scheduler.schedule(make_request())

    assert result.status is ExecutionStatus.FAILED
    assert executor.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("executor", "commit"),
    [
        (SpyExecutor(error=RuntimeError("executor failed")), SpyCommitGate()),
        (SpyExecutor(status=ExecutionStatus.SUCCESS), SpyCommitGate()),
        (SpyExecutor(), SpyCommitGate(status=ExecutionStatus.FAILED)),
    ],
)
async def test_unsafe_execution_or_commit_failure_rolls_back(
    executor: SpyExecutor, commit: SpyCommitGate
) -> None:
    rollback = SpyRollbackManager()
    scheduler, _ = make_scheduler(executor=executor, commit=commit, rollback=rollback)

    result = await scheduler.schedule(make_request())

    assert result.status is ExecutionStatus.ROLLED_BACK
    assert rollback.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dependency", "expected_error"),
    [
        (SpyExecutor(request_id="wrong-request"), "CORRELATION_MISMATCH"),
        (SpyExecutor(checkpoint_id="wrong-checkpoint"), "CORRELATION_MISMATCH"),
        (SpyDeepChecker(request_id="wrong-request"), "CORRELATION_MISMATCH"),
        (SpyCommitGate(request_id="wrong-request"), "CORRELATION_MISMATCH"),
        (SpyCommitGate(checkpoint_id="wrong-checkpoint"), "CORRELATION_MISMATCH"),
    ],
)
async def test_cross_module_mismatch_fails_closed_and_rolls_back(
    dependency: object, expected_error: str
) -> None:
    kwargs: dict[str, object]
    if isinstance(dependency, SpyExecutor):
        kwargs = {"executor": dependency}
    elif isinstance(dependency, SpyDeepChecker):
        kwargs = {"deep": dependency}
    else:
        kwargs = {"commit": dependency}
    rollback = SpyRollbackManager()
    scheduler, _ = make_scheduler(**kwargs, rollback=rollback)

    result = await scheduler.schedule(make_request())

    assert result.status is ExecutionStatus.ROLLED_BACK
    assert result.error_code == expected_error
    assert rollback.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dependency", "started", "finished"),
    [
        (
            SpyDeepChecker(request_id="wrong-request"),
            AuditEventType.DEEP_CHECK_STARTED,
            AuditEventType.DEEP_CHECK_FINISHED,
        ),
        (
            SpyCommitGate(request_id="wrong-request"),
            AuditEventType.COMMIT_STARTED,
            AuditEventType.COMMIT_FINISHED,
        ),
    ],
)
async def test_failed_stage_records_matching_finished_event(
    dependency: object, started: AuditEventType, finished: AuditEventType
) -> None:
    kwargs = (
        {"deep": dependency}
        if isinstance(dependency, SpyDeepChecker)
        else {"commit": dependency}
    )
    scheduler, container = make_scheduler(**kwargs)

    await scheduler.schedule(make_request())

    assert isinstance(container.audit_recorder, InMemoryAuditRecorder)
    event_types = [
        event.event_type
        for event in container.audit_recorder.events_for("task-sandbox")
    ]
    assert started in event_types
    assert finished in event_types


@pytest.mark.asyncio
async def test_mismatched_rollback_records_finish_and_fails_closed() -> None:
    rollback = SpyRollbackManager(request_id="wrong-request")
    scheduler, container = make_scheduler(
        deep=SpyDeepChecker(passed=False), rollback=rollback
    )

    result = await scheduler.schedule(make_request())

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code == "CORRELATION_MISMATCH"
    assert isinstance(container.audit_recorder, InMemoryAuditRecorder)
    event_types = [
        event.event_type
        for event in container.audit_recorder.events_for("task-sandbox")
    ]
    assert AuditEventType.ROLLBACK_STARTED in event_types
    assert AuditEventType.ROLLBACK_FINISHED in event_types


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dependency_name", "dependency"),
    [
        ("executor", CancellingExecutor()),
        ("deep", CancellingDeepChecker()),
        ("commit", CancellingCommitGate()),
    ],
)
async def test_cancellation_after_checkpoint_rolls_back_before_propagating(
    dependency_name: str, dependency: object
) -> None:
    rollback = SpyRollbackManager()
    scheduler, _ = make_scheduler(**{dependency_name: dependency}, rollback=rollback)

    with pytest.raises(asyncio.CancelledError):
        await scheduler.schedule(make_request())

    assert rollback.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type",
    [
        AuditEventType.CHECKPOINT_CREATED,
        AuditEventType.EXECUTION_STARTED,
        AuditEventType.EXECUTION_FINISHED,
        AuditEventType.DEEP_CHECK_STARTED,
        AuditEventType.DEEP_CHECK_FINISHED,
        AuditEventType.COMMIT_STARTED,
    ],
)
async def test_cancellation_at_post_checkpoint_audit_boundary_rolls_back(
    event_type: AuditEventType,
) -> None:
    rollback = SpyRollbackManager()
    recorder = CancellingAuditRecorder(event_type)
    scheduler, _ = make_scheduler(rollback=rollback, recorder=recorder)

    with pytest.raises(asyncio.CancelledError):
        await scheduler.schedule(make_request())

    assert rollback.calls == 1
