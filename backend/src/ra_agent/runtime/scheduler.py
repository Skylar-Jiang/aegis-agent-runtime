from datetime import UTC, datetime

from ra_agent.audit import AuditEventSink
from ra_agent.contracts import (
    AuditEvent,
    AuditEventType,
    PermissionStatus,
    PolicyDecision,
    RiskLevel,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution import ToolExecutor
from ra_agent.security import PermissionGate, PolicyEngine, RiskClassifier


class RuntimeScheduler:
    """Minimal Phase 0 wiring; only FAST_EXECUTE is connected to a mock executor."""

    def __init__(
        self,
        *,
        classifier: RiskClassifier,
        policy: PolicyEngine,
        permission_gate: PermissionGate,
        executor: ToolExecutor,
        audit: AuditEventSink,
    ) -> None:
        self.classifier = classifier
        self.policy = policy
        self.permission_gate = permission_gate
        self.executor = executor
        self.audit = audit

    async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
        await self._emit(request, 1, AuditEventType.TOOL_REQUESTED, "tool requested")
        verdict = await self.classifier.classify(request)
        decision = await self.policy.decide(verdict)
        await self._emit(
            request,
            2,
            AuditEventType.RISK_CLASSIFIED,
            "risk classified",
            risk_level=verdict.risk_level,
            decision=decision,
        )
        permission = await self.permission_gate.check(request)
        await self._emit(request, 3, AuditEventType.PERMISSION_CHECKED, "permission checked")

        if decision is not PolicyDecision.FAST_EXECUTE:
            raise NotImplementedError("Non-fast execution paths are outside Phase 0")
        if permission.status not in {PermissionStatus.GRANTED, PermissionStatus.NOT_REQUIRED}:
            raise PermissionError(permission.reason)
        return await self.executor.execute(request)

    async def _emit(
        self,
        request: ToolCallRequest,
        sequence_number: int,
        event_type: AuditEventType,
        summary: str,
        risk_level: RiskLevel | None = None,
        decision: PolicyDecision | None = None,
    ) -> None:
        event = AuditEvent(
            event_id=f"{request.request_id}-{sequence_number}",
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            sequence_number=sequence_number,
            event_type=event_type,
            timestamp=datetime.now(UTC),
            actor="runtime-scheduler",
            status="recorded",
            risk_level=risk_level,
            decision=decision,
            summary=summary,
        )
        await self.audit.emit(event)
