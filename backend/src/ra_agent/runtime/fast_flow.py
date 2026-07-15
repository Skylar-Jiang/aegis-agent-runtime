from datetime import UTC, datetime

from ra_agent.audit import AuditRecorder
from ra_agent.contracts import (
    ApprovalDecision,
    AuditEventType,
    ExecutionStatus,
    PolicyDecision,
    RiskLevel,
    StepStatus,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution import ToolExecutor

from .correlation import CorrelationError, validate_execution
from .state_machine import transition


class FastExecutionFlow:
    def __init__(self, *, executor: ToolExecutor, audit_recorder: AuditRecorder) -> None:
        self.executor = executor
        self.audit_recorder = audit_recorder

    async def run(
        self,
        request: ToolCallRequest,
        risk_level: RiskLevel,
        decision: PolicyDecision = PolicyDecision.FAST_EXECUTE,
        *,
        start_state: StepStatus = StepStatus.RISK_CLASSIFYING,
        approval_decision: ApprovalDecision | None = None,
    ) -> ToolExecutionResult:
        state = transition(start_state, StepStatus.READY)
        state = transition(state, StepStatus.EXECUTING_FAST)
        started_at = datetime.now(UTC)
        await self._record(
            request,
            AuditEventType.EXECUTION_STARTED,
            state,
            "fast execution started",
            risk_level,
            decision,
        )
        try:
            execution = await self.executor.execute(request, approval_decision=approval_decision)
        except Exception as error:
            return await self._fail(
                request,
                state,
                str(error) or type(error).__name__,
                risk_level,
                decision,
                started_at,
            )

        finished_at = datetime.now(UTC)
        try:
            validate_execution(request, execution)
        except CorrelationError as error:
            return await self._fail(
                request,
                state,
                str(error),
                risk_level,
                decision,
                started_at,
                finished_at,
                error.error_code,
            )
        if execution.status is not ExecutionStatus.SUCCESS:
            return await self._fail(
                request,
                state,
                execution.error or f"Executor returned {execution.status.value}",
                risk_level,
                decision,
                execution.started_at or started_at,
                execution.finished_at or finished_at,
                execution.error_code,
            )

        state = transition(state, StepStatus.COMMITTED)
        await self._record(
            request,
            AuditEventType.EXECUTION_FINISHED,
            state,
            "fast execution finished",
            risk_level,
            decision,
        )
        return execution.model_copy(
            update={
                "status": ExecutionStatus.COMMITTED,
                "started_at": execution.started_at or started_at,
                "finished_at": execution.finished_at or finished_at,
            }
        )

    async def _fail(
        self,
        request: ToolCallRequest,
        state: StepStatus,
        reason: str,
        risk_level: RiskLevel,
        decision: PolicyDecision,
        started_at: datetime,
        finished_at: datetime | None = None,
        error_code: str | None = None,
    ) -> ToolExecutionResult:
        state = transition(state, StepStatus.FAILED)
        finished_at = finished_at or datetime.now(UTC)
        await self._record(
            request,
            AuditEventType.EXECUTION_FINISHED,
            state,
            "fast execution finished with failure",
            risk_level,
            decision,
            {"reason": reason},
        )
        await self.audit_recorder.record(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            event_type=AuditEventType.STEP_FAILED,
            actor="runtime-fast-flow",
            status=state.value,
            risk_level=risk_level,
            decision=decision,
            summary="fast execution failed",
            details={"reason": reason, **({"error_code": error_code} if error_code else {})},
        )
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.FAILED,
            error=reason,
            error_code=error_code,
            started_at=started_at,
            finished_at=finished_at,
        )

    async def _record(
        self,
        request: ToolCallRequest,
        event_type: AuditEventType,
        state: StepStatus,
        summary: str,
        risk_level: RiskLevel,
        decision: PolicyDecision,
        details: dict[str, object] | None = None,
    ) -> None:
        await self.audit_recorder.record(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            event_type=event_type,
            actor="runtime-fast-flow",
            status=state.value,
            risk_level=risk_level,
            decision=decision,
            summary=summary,
            details=details,
        )
