from datetime import UTC, datetime, timedelta

from ra_agent.audit import AuditRecorder
from ra_agent.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    AuditEventType,
    ExecutionStatus,
    PolicyDecision,
    RecoverabilityType,
    RiskVerdict,
    StepStatus,
    ToolCallRequest,
    ToolExecutionResult,
    ToolSpec,
)
from ra_agent.core.ids import new_id
from ra_agent.security import (
    ApprovalService,
    PermissionGate,
    PolicyEngine,
    RiskClassifier,
)
from ra_agent.tools import ToolRegistry

from .correlation import (
    CorrelationError,
    validate_approval_decision,
    validate_approval_request,
    validate_risk,
)
from .fast_flow import FastExecutionFlow
from .idempotency import RequestExecutionRegistry, request_fingerprint
from .permissions import permission_failure_reason
from .sandbox_flow import SandboxFlow


class ApprovalFlow:
    """Owns the recoverable two-stage approval flow and its one-time resume."""

    def __init__(
        self,
        *,
        approval_service: ApprovalService,
        audit_recorder: AuditRecorder,
        request_registry: RequestExecutionRegistry,
        tool_registry: ToolRegistry,
        classifier: RiskClassifier,
        policy: PolicyEngine,
        permission_gate: PermissionGate,
        fast_flow: FastExecutionFlow,
        sandbox_flow: SandboxFlow,
        ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        self.approval_service = approval_service
        self.audit_recorder = audit_recorder
        self.request_registry = request_registry
        self.tool_registry = tool_registry
        self.classifier = classifier
        self.policy = policy
        self.permission_gate = permission_gate
        self.fast_flow = fast_flow
        self.sandbox_flow = sandbox_flow
        self.ttl = ttl

    async def request_approval(
        self, request: ToolCallRequest, verdict: RiskVerdict
    ) -> ToolExecutionResult:
        now = datetime.now(UTC)
        approval = ApprovalRequest(
            approval_id=new_id("approval"),
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            tool_name=request.tool_name,
            request_fingerprint=request_fingerprint(request),
            reason=verdict.reason,
            requested_at=now,
            expires_at=now + self.ttl,
        )
        try:
            stored = await self.approval_service.create(approval)
            validate_approval_request(request, request_fingerprint(request), stored)
        except CorrelationError as error:
            return await self._failure(request, str(error), error.error_code)
        except Exception as error:
            return await self._failure(
                request,
                str(error) or type(error).__name__,
                "APPROVAL_CREATE_FAILED",
            )
        await self._record(
            request,
            AuditEventType.APPROVAL_REQUESTED,
            StepStatus.WAITING_APPROVAL,
            "approval requested",
            {
                "approval_id": stored.approval_id,
                "tool_name": stored.tool_name,
                "expires_at": stored.expires_at.isoformat(),
            },
        )
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.WAITING_APPROVAL,
            output={"approval_id": stored.approval_id},
        )

    async def resume(self, request: ToolCallRequest, approval_id: str) -> ToolExecutionResult:
        try:
            approval, decision = await self._load(request, approval_id)
        except (CorrelationError, KeyError, ValueError) as error:
            return await self._failure(request, str(error) or "Approval mismatch")

        if decision is None:
            claim = await self.request_registry.claim(request)
            if claim.conflict:
                return await self._request_id_conflict(request)
            if claim.owns_execution:
                return await self._failure(
                    request, "Approval request is not registered for waiting"
                )
            return await self.request_registry.wait(claim)

        claim = await self.request_registry.claim_resume(request)
        if claim.conflict:
            return await self._request_id_conflict(request)
        if not claim.owns_execution:
            return await self.request_registry.wait(claim)

        try:
            consumed = await self.approval_service.consume(approval.approval_id)
            validate_approval_decision(approval, consumed)
            await self._record_decision(consumed)
            if consumed.status in {ApprovalStatus.DENIED, ApprovalStatus.EXPIRED}:
                result = self._result(
                    request,
                    ExecutionStatus.BLOCKED,
                    consumed.reason,
                    f"APPROVAL_{consumed.status.value}",
                )
            elif consumed.status is ApprovalStatus.GRANTED:
                result = await self._run_approved(request, consumed)
            else:
                result = await self._failure(
                    request,
                    f"Approval is not granted: {consumed.status.value}",
                    "APPROVAL_NOT_GRANTED",
                )
        except BaseException as error:
            await self.request_registry.fail(claim, error)
            raise
        await self.request_registry.complete(claim, result)
        return result

    async def _load(
        self, request: ToolCallRequest, approval_id: str
    ) -> tuple[ApprovalRequest, ApprovalDecision | None]:
        approval = await self.approval_service.get_request(approval_id)
        validate_approval_request(request, request_fingerprint(request), approval)
        decision = await self.approval_service.get_decision(approval_id)
        if decision is not None:
            validate_approval_decision(approval, decision)
        return approval, decision

    async def _run_approved(
        self, request: ToolCallRequest, decision: ApprovalDecision
    ) -> ToolExecutionResult:
        try:
            tool_spec = self.tool_registry.get_spec(request.tool_name)
            verdict = await self.classifier.classify(request)
            validate_risk(request, verdict)
            current_decision = await self.policy.decide(verdict)
        except CorrelationError as error:
            return await self._failure(request, str(error), error.error_code)
        except Exception as error:
            return await self._failure(request, str(error) or type(error).__name__)

        if current_decision is PolicyDecision.BLOCK:
            return await self._blocked(
                request, verdict.reason, verdict.risk_level, current_decision
            )

        routed_decision = self._approved_route(tool_spec, current_decision)
        if routed_decision is PolicyDecision.BLOCK:
            return await self._blocked(
                request,
                "Approval cannot enable non-reversible or disabled Mock execution",
                verdict.risk_level,
                routed_decision,
            )

        try:
            permission = await self.permission_gate.check(request, tool_spec)
        except Exception as error:
            return await self._failure(request, str(error) or type(error).__name__)
        await self._record(
            request,
            AuditEventType.PERMISSION_CHECKED,
            StepStatus.WAITING_APPROVAL,
            "permission rechecked after approval",
            {
                "allowed": permission.allowed,
                "requires_approval": permission.requires_approval,
                "reason": permission.reason,
            },
        )
        try:
            failure = permission_failure_reason(
                request, tool_spec, permission, approval_satisfied=True
            )
        except CorrelationError as error:
            return await self._failure(request, str(error), error.error_code)
        if failure is not None:
            return self._result(
                request,
                ExecutionStatus.BLOCKED,
                failure,
                "PERMISSION_REJECTED",
            )

        if routed_decision is PolicyDecision.SANDBOX_CHECK:
            return await self.sandbox_flow.run(
                request,
                verdict,
                start_state=StepStatus.READY,
                approval_decision=decision,
            )
        return await self.fast_flow.run(
            request,
            verdict,
            routed_decision,
            start_state=StepStatus.WAITING_APPROVAL,
            approval_decision=decision,
        )

    @staticmethod
    def _approved_route(tool_spec: ToolSpec, current_decision: PolicyDecision) -> PolicyDecision:
        if (
            tool_spec.reversibility is RecoverabilityType.NON_REVERSIBLE
            or tool_spec.sandbox_mode.startswith("DISABLED")
        ):
            return PolicyDecision.BLOCK
        if current_decision is PolicyDecision.SANDBOX_CHECK:
            return PolicyDecision.SANDBOX_CHECK
        if tool_spec.sandbox_mode != "NONE":
            return PolicyDecision.SANDBOX_CHECK
        return PolicyDecision.FAST_EXECUTE

    async def _record_decision(self, decision: ApprovalDecision) -> None:
        granted = decision.status is ApprovalStatus.GRANTED
        await self.audit_recorder.record(
            task_id=decision.task_id,
            step_id=decision.step_id,
            request_id=decision.request_id,
            event_type=(
                AuditEventType.APPROVAL_GRANTED if granted else AuditEventType.APPROVAL_DENIED
            ),
            actor="runtime-approval-flow",
            status=(StepStatus.READY.value if granted else StepStatus.BLOCKED.value),
            summary=("approval granted" if granted else "approval denied or expired"),
            details={
                "approval_id": decision.approval_id,
                "approval_status": decision.status.value,
                "decided_by": decision.decided_by,
                "reason": decision.reason,
            },
        )

    async def _blocked(
        self, request: ToolCallRequest, reason: str, risk_level, decision: PolicyDecision
    ) -> ToolExecutionResult:
        await self.audit_recorder.record(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            event_type=AuditEventType.TOOL_BLOCKED,
            actor="runtime-approval-flow",
            status=StepStatus.BLOCKED.value,
            risk_level=risk_level,
            decision=decision,
            summary="approved request remains blocked",
            details={"reason": reason},
        )
        return self._result(request, ExecutionStatus.BLOCKED, reason)

    async def _failure(
        self,
        request: ToolCallRequest,
        reason: str,
        error_code: str = "APPROVAL_MISMATCH",
    ) -> ToolExecutionResult:
        await self.audit_recorder.record(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            event_type=AuditEventType.STEP_FAILED,
            actor="runtime-approval-flow",
            status=StepStatus.WAITING_APPROVAL.value,
            decision=PolicyDecision.REQUEST_APPROVAL,
            summary="approval resume failed",
            details={"reason": reason, "error_code": error_code},
        )
        return self._result(request, ExecutionStatus.FAILED, reason, error_code)

    async def _request_id_conflict(self, request: ToolCallRequest) -> ToolExecutionResult:
        reason = "request_id was already used for different request semantics"
        await self.audit_recorder.record(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            event_type=AuditEventType.STEP_FAILED,
            actor="runtime-approval-flow",
            status=StepStatus.FAILED.value,
            summary="approval resume request id conflict",
            details={"reason": reason, "error_code": "REQUEST_ID_CONFLICT"},
        )
        return self._result(request, ExecutionStatus.FAILED, reason, "REQUEST_ID_CONFLICT")

    async def _record(
        self,
        request: ToolCallRequest,
        event_type: AuditEventType,
        state: StepStatus,
        summary: str,
        details: dict[str, object],
    ) -> None:
        await self.audit_recorder.record(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            event_type=event_type,
            actor="runtime-approval-flow",
            status=state.value,
            summary=summary,
            details=details,
        )

    @staticmethod
    def _result(
        request: ToolCallRequest,
        status: ExecutionStatus,
        reason: str,
        error_code: str | None = None,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=status,
            error=reason,
            error_code=error_code,
        )
