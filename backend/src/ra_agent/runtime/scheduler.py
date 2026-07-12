from datetime import datetime

from ra_agent.audit import AuditRecorder
from ra_agent.contracts import (
    AuditEventType,
    ExecutionStatus,
    PolicyDecision,
    RiskLevel,
    StepStatus,
    ToolCallRequest,
    ToolExecutionResult,
    ToolSpec,
)
from ra_agent.execution import ToolExecutor
from ra_agent.security import PermissionGate, PolicyEngine, RiskClassifier
from ra_agent.tools import ToolRegistry

from .approval_flow import ApprovalFlow
from .correlation import CorrelationError, validate_risk
from .fast_flow import FastExecutionFlow
from .idempotency import RequestExecutionRegistry
from .permissions import permission_failure_reason
from .sandbox_flow import SandboxFlow
from .state_machine import transition


class RuntimeScheduler:
    """The only Runtime entry point that may invoke a ToolExecutor."""

    def __init__(
        self,
        *,
        classifier: RiskClassifier,
        policy: PolicyEngine,
        permission_gate: PermissionGate,
        executor: ToolExecutor,
        audit_recorder: AuditRecorder,
        tool_registry: ToolRegistry,
        request_registry: RequestExecutionRegistry,
        sandbox_flow: SandboxFlow | None = None,
        approval_flow: ApprovalFlow | None = None,
        fast_flow: FastExecutionFlow | None = None,
    ) -> None:
        self.classifier = classifier
        self.policy = policy
        self.permission_gate = permission_gate
        self.audit_recorder = audit_recorder
        self.tool_registry = tool_registry
        self.request_registry = request_registry
        self.sandbox_flow = sandbox_flow
        self.approval_flow = approval_flow
        self.fast_flow = fast_flow or FastExecutionFlow(
            executor=executor, audit_recorder=audit_recorder
        )

    async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
        claim = await self.request_registry.claim(request)
        if claim.conflict:
            return await self._request_id_conflict(request)
        if not claim.owns_execution:
            return await self.request_registry.wait(claim)
        try:
            result = await self._schedule_once(request)
        except BaseException as error:
            await self.request_registry.fail(claim, error)
            raise
        await self.request_registry.complete(claim, result)
        return result

    async def resume_after_approval(
        self, request: ToolCallRequest, approval_id: str
    ) -> ToolExecutionResult:
        if self.approval_flow is None:
            reason = "Approval dependencies are not configured"
            await self._record_failure(request, StepStatus.WAITING_APPROVAL, reason)
            return self._result(request, ExecutionStatus.FAILED, reason)
        return await self.approval_flow.resume(request, approval_id)

    async def _schedule_once(self, request: ToolCallRequest) -> ToolExecutionResult:
        state = StepStatus.PLANNED
        await self._record(request, AuditEventType.TOOL_REQUESTED, state, "tool requested")
        try:
            tool_spec = self.tool_registry.get_spec(request.tool_name)
        except KeyError:
            state = transition(state, StepStatus.FAILED)
            reason = f"Unknown tool: {request.tool_name}"
            await self._record_failure(request, state, reason)
            return self._result(request, ExecutionStatus.FAILED, reason)

        state = transition(state, StepStatus.RISK_CLASSIFYING)
        try:
            verdict = await self.classifier.classify(request)
            validate_risk(request, verdict)
            decision = await self.policy.decide(verdict)
        except CorrelationError as error:
            state = transition(state, StepStatus.FAILED)
            await self._record_failure(request, state, str(error), error_code=error.error_code)
            return self._result(
                request,
                ExecutionStatus.FAILED,
                str(error),
                error_code=error.error_code,
            )
        except Exception as error:
            state = transition(state, StepStatus.FAILED)
            reason = str(error) or type(error).__name__
            await self._record_failure(request, state, reason)
            return self._result(request, ExecutionStatus.FAILED, reason)

        await self._record(
            request,
            AuditEventType.RISK_CLASSIFIED,
            state,
            "risk classified",
            risk_level=verdict.risk_level,
            decision=decision,
            details={"reason": verdict.reason, "signals": verdict.signals},
        )
        if decision is PolicyDecision.BLOCK:
            return await self._block(request, state, verdict.reason, verdict.risk_level, decision)
        if decision is PolicyDecision.REQUEST_APPROVAL:
            if self.approval_flow is None:
                state = transition(state, StepStatus.FAILED)
                reason = "REQUEST_APPROVAL dependencies are not configured"
                await self._record_failure(request, state, reason)
                return self._result(request, ExecutionStatus.FAILED, reason)
            return await self.approval_flow.request_approval(request, verdict)
        if decision not in {PolicyDecision.FAST_EXECUTE, PolicyDecision.SANDBOX_CHECK}:
            state = transition(state, StepStatus.FAILED)
            reason = f"Unsupported policy decision: {decision}"
            await self._record_failure(request, state, reason)
            return self._result(request, ExecutionStatus.FAILED, reason)

        authorization_failure = await self._authorize(
            request, tool_spec, state, verdict.risk_level, decision
        )
        if authorization_failure is not None:
            return authorization_failure
        if decision is PolicyDecision.SANDBOX_CHECK:
            if self.sandbox_flow is None:
                state = transition(state, StepStatus.FAILED)
                reason = "SANDBOX_CHECK dependencies are not configured"
                await self._record_failure(request, state, reason)
                return self._result(request, ExecutionStatus.FAILED, reason)
            return await self.sandbox_flow.run(request, verdict)
        return await self.fast_flow.run(request, verdict.risk_level, decision)

    async def _authorize(
        self,
        request: ToolCallRequest,
        tool_spec: ToolSpec,
        state: StepStatus,
        risk_level: RiskLevel,
        decision: PolicyDecision,
    ) -> ToolExecutionResult | None:
        try:
            permission = await self.permission_gate.check(request, tool_spec)
        except Exception as error:
            state = transition(state, StepStatus.FAILED)
            reason = str(error) or type(error).__name__
            await self._record_failure(
                request, state, reason, risk_level=risk_level, decision=decision
            )
            return self._result(request, ExecutionStatus.FAILED, reason)
        await self._record(
            request,
            AuditEventType.PERMISSION_CHECKED,
            state,
            "permission checked",
            risk_level=risk_level,
            decision=decision,
            details={
                "allowed": permission.allowed,
                "requires_approval": permission.requires_approval,
                "reason": permission.reason,
            },
        )
        try:
            failure = permission_failure_reason(request, tool_spec, permission)
        except CorrelationError as error:
            failed_state = transition(state, StepStatus.FAILED)
            await self._record_failure(
                request,
                failed_state,
                str(error),
                risk_level=risk_level,
                decision=decision,
                error_code=error.error_code,
            )
            return self._result(
                request,
                ExecutionStatus.FAILED,
                str(error),
                error_code=error.error_code,
            )
        if failure is None:
            return None
        return await self._block(request, state, failure, risk_level, decision)

    async def _block(
        self,
        request: ToolCallRequest,
        state: StepStatus,
        reason: str,
        risk_level: RiskLevel,
        decision: PolicyDecision,
    ) -> ToolExecutionResult:
        state = transition(state, StepStatus.BLOCKED)
        await self._record(
            request,
            AuditEventType.TOOL_BLOCKED,
            state,
            "tool blocked",
            risk_level=risk_level,
            decision=decision,
            details={"reason": reason},
        )
        return self._result(request, ExecutionStatus.BLOCKED, reason)

    async def _request_id_conflict(self, request: ToolCallRequest) -> ToolExecutionResult:
        state = StepStatus.PLANNED
        await self._record(
            request,
            AuditEventType.TOOL_REQUESTED,
            state,
            "conflicting request id received",
            details={"error_code": "REQUEST_ID_CONFLICT"},
        )
        state = transition(state, StepStatus.FAILED)
        reason = "request_id was already used for different request semantics"
        await self._record_failure(request, state, reason, error_code="REQUEST_ID_CONFLICT")
        return self._result(
            request,
            ExecutionStatus.FAILED,
            reason,
            error_code="REQUEST_ID_CONFLICT",
        )

    async def _record_failure(
        self,
        request: ToolCallRequest,
        state: StepStatus,
        reason: str,
        *,
        risk_level: RiskLevel | None = None,
        decision: PolicyDecision | None = None,
        error_code: str | None = None,
    ) -> None:
        await self._record(
            request,
            AuditEventType.STEP_FAILED,
            state,
            "runtime step failed",
            risk_level=risk_level,
            decision=decision,
            details={"reason": reason, **({"error_code": error_code} if error_code else {})},
        )

    async def _record(
        self,
        request: ToolCallRequest,
        event_type: AuditEventType,
        state: StepStatus,
        summary: str,
        *,
        risk_level: RiskLevel | None = None,
        decision: PolicyDecision | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        await self.audit_recorder.record(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            event_type=event_type,
            actor="runtime-scheduler",
            status=state.value,
            risk_level=risk_level,
            decision=decision,
            summary=summary,
            details=details,
        )

    @staticmethod
    def _result(
        request: ToolCallRequest,
        status: ExecutionStatus,
        error: str,
        *,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        error_code: str | None = None,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=status,
            error=error,
            error_code=error_code,
            started_at=started_at,
            finished_at=finished_at,
        )
