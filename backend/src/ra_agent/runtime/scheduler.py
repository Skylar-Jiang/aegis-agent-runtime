from datetime import UTC, datetime

from ra_agent.audit import AuditRecorder
from ra_agent.contracts import (
    AuditEventType,
    ExecutionStatus,
    PermissionCheckResult,
    PermissionStatus,
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

from .idempotency import RequestExecutionRegistry
from .state_machine import transition


class RuntimeScheduler:
    """The only runtime entry point that may invoke a ToolExecutor."""

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
    ) -> None:
        self.classifier = classifier
        self.policy = policy
        self.permission_gate = permission_gate
        self.executor = executor
        self.audit_recorder = audit_recorder
        self.tool_registry = tool_registry
        self.request_registry = request_registry

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

    async def _schedule_once(self, request: ToolCallRequest) -> ToolExecutionResult:
        state = StepStatus.PLANNED
        await self._record(
            request,
            AuditEventType.TOOL_REQUESTED,
            state,
            "tool requested",
        )

        try:
            tool_spec = self.tool_registry.get(request.tool_name)
        except KeyError:
            state = transition(state, StepStatus.FAILED)
            reason = f"Unknown tool: {request.tool_name}"
            await self._record_failure(request, state, reason)
            return self._result(request, ExecutionStatus.FAILED, reason)

        state = transition(state, StepStatus.RISK_CLASSIFYING)
        try:
            verdict = await self.classifier.classify(request)
            decision = await self.policy.decide(verdict)
        except Exception as exc:
            state = transition(state, StepStatus.FAILED)
            reason = str(exc) or type(exc).__name__
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
            state = transition(state, StepStatus.BLOCKED)
            await self._record(
                request,
                AuditEventType.TOOL_BLOCKED,
                state,
                "tool blocked by policy",
                risk_level=verdict.risk_level,
                decision=decision,
                details={"reason": verdict.reason},
            )
            return self._result(request, ExecutionStatus.BLOCKED, verdict.reason)

        if decision is not PolicyDecision.FAST_EXECUTE:
            state = transition(state, StepStatus.FAILED)
            reason = f"Runtime Phase 1 does not support policy {decision.value}"
            await self._record_failure(
                request,
                state,
                reason,
                risk_level=verdict.risk_level,
                decision=decision,
            )
            return self._result(request, ExecutionStatus.FAILED, reason)

        try:
            permission = await self.permission_gate.check(request, tool_spec)
        except Exception as exc:
            state = transition(state, StepStatus.FAILED)
            reason = str(exc) or type(exc).__name__
            await self._record_failure(
                request,
                state,
                reason,
                risk_level=verdict.risk_level,
                decision=decision,
            )
            return self._result(request, ExecutionStatus.FAILED, reason)
        await self._record(
            request,
            AuditEventType.PERMISSION_CHECKED,
            state,
            "permission checked",
            risk_level=verdict.risk_level,
            decision=decision,
            details={
                "allowed": permission.allowed,
                "requires_approval": permission.requires_approval,
                "reason": permission.reason,
            },
        )
        permission_failure = self._permission_failure_reason(request, tool_spec, permission)
        if permission_failure is not None:
            state = transition(state, StepStatus.BLOCKED)
            await self._record(
                request,
                AuditEventType.TOOL_BLOCKED,
                state,
                "tool blocked by permissions",
                risk_level=verdict.risk_level,
                decision=decision,
                details={"reason": permission_failure},
            )
            return self._result(request, ExecutionStatus.BLOCKED, permission_failure)

        state = transition(state, StepStatus.READY)
        state = transition(state, StepStatus.EXECUTING_FAST)
        started_at = datetime.now(UTC)
        await self._record(
            request,
            AuditEventType.EXECUTION_STARTED,
            state,
            "fast execution started",
            risk_level=verdict.risk_level,
            decision=decision,
        )

        try:
            execution = await self.executor.execute(request)
        except Exception as exc:
            state = transition(state, StepStatus.FAILED)
            reason = str(exc) or type(exc).__name__
            await self._record(
                request,
                AuditEventType.EXECUTION_FINISHED,
                state,
                "fast execution finished with failure",
                risk_level=verdict.risk_level,
                decision=decision,
                details={"reason": reason},
            )
            await self._record_failure(
                request,
                state,
                reason,
                risk_level=verdict.risk_level,
                decision=decision,
            )
            return self._result(
                request,
                ExecutionStatus.FAILED,
                reason,
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )

        finished_at = datetime.now(UTC)
        if execution.status is not ExecutionStatus.SUCCESS:
            state = transition(state, StepStatus.FAILED)
            reason = execution.error or f"Executor returned {execution.status.value}"
            await self._record(
                request,
                AuditEventType.EXECUTION_FINISHED,
                state,
                "fast execution finished with failure",
                risk_level=verdict.risk_level,
                decision=decision,
                details={"reason": reason},
            )
            await self._record_failure(
                request,
                state,
                reason,
                risk_level=verdict.risk_level,
                decision=decision,
            )
            return execution.model_copy(
                update={
                    "status": ExecutionStatus.FAILED,
                    "error": reason,
                    "started_at": execution.started_at or started_at,
                    "finished_at": execution.finished_at or finished_at,
                }
            )

        state = transition(state, StepStatus.COMMITTED)
        await self._record(
            request,
            AuditEventType.EXECUTION_FINISHED,
            state,
            "fast execution finished",
            risk_level=verdict.risk_level,
            decision=decision,
        )
        return execution.model_copy(
            update={
                "status": ExecutionStatus.COMMITTED,
                "started_at": execution.started_at or started_at,
                "finished_at": execution.finished_at or finished_at,
            }
        )

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
        await self._record_failure(
            request,
            state,
            reason,
            error_code="REQUEST_ID_CONFLICT",
        )
        return self._result(
            request,
            ExecutionStatus.FAILED,
            reason,
            error_code="REQUEST_ID_CONFLICT",
        )

    @staticmethod
    def _permission_failure_reason(
        request: ToolCallRequest,
        tool_spec: ToolSpec,
        permission: PermissionCheckResult,
    ) -> str | None:
        if permission.request_id != request.request_id:
            return "Permission result request_id does not match the current request"
        if any(item.request_id != request.request_id for item in permission.decisions):
            return "Permission decision request_id does not match the current request"

        required = set(tool_spec.required_permissions)
        decided = [item.permission for item in permission.decisions]
        decided_set = set(decided)
        if len(decided) != len(decided_set):
            return "Permission result contains duplicate permission decisions"
        missing = required - decided_set
        if missing:
            names = ", ".join(sorted(item.value for item in missing))
            return f"Permission gate returned missing permission decisions: {names}"
        extra = decided_set - required
        if extra:
            names = ", ".join(sorted(item.value for item in extra))
            return f"Permission gate returned unexpected permission decisions: {names}"
        if permission.requires_approval:
            return "Permission result requires approval and cannot use FAST_EXECUTE"

        executable_statuses = {
            PermissionStatus.GRANTED,
            PermissionStatus.NOT_REQUIRED,
        }
        decisions_allow = all(item.status in executable_statuses for item in permission.decisions)
        if permission.allowed != decisions_allow:
            return "Permission result allowed flag contradicts its decisions"
        if not permission.allowed:
            return permission.reason or "Permission result does not allow execution"
        return None

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
