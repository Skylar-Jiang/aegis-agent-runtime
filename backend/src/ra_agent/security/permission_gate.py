from typing import Protocol

from ra_agent.contracts import (
    PermissionCheckResult,
    PermissionDecision,
    PermissionStatus,
    ToolCallRequest,
    ToolSpec,
)

from .adaptive_approval import AdaptiveApprovalEvaluator
from .rule_engine import RuleEngine


class PermissionGate(Protocol):
    async def check(
        self, request: ToolCallRequest, tool_spec: ToolSpec
    ) -> PermissionCheckResult: ...


class RuleBasedPermissionGate:
    """Evaluates every trusted ToolSpec permission and fails closed on drift."""

    _EXECUTABLE_STATUSES = {
        PermissionStatus.GRANTED,
        PermissionStatus.NOT_REQUIRED,
    }

    def __init__(
        self,
        rules: RuleEngine,
        *,
        approval_evaluator: AdaptiveApprovalEvaluator | None = None,
    ) -> None:
        self.rules = rules
        self.approval_evaluator = approval_evaluator or AdaptiveApprovalEvaluator(rules)

    async def check(self, request: ToolCallRequest, tool_spec: ToolSpec) -> PermissionCheckResult:
        if not self.rules.valid:
            return self._deny_all(
                request,
                tool_spec,
                f"Security configuration is invalid: {self.rules.error}",
            )
        if tool_spec.name != request.tool_name:
            return self._deny_all(
                request,
                tool_spec,
                "Trusted ToolSpec name does not match the current request",
            )

        configured = self.rules.permissions_for(tool_spec.name)
        if configured is None:
            return self._deny_all(
                request,
                tool_spec,
                f"No permission policy exists for tool {tool_spec.name}",
            )
        required = tuple(tool_spec.required_permissions)
        if len(required) != len(set(required)):
            return self._deny_all(
                request,
                tool_spec,
                "Trusted ToolSpec contains duplicate required permissions",
            )
        if set(configured) != set(required):
            return self._deny_all(
                request,
                tool_spec,
                "Configured permissions do not match trusted ToolSpec permissions",
            )

        decisions = [
            PermissionDecision(
                request_id=request.request_id,
                permission=permission,
                status=self.rules.permission_status_for(tool_spec.name, permission),
                reason=f"Configured status for {tool_spec.name}:{permission.value}",
            )
            for permission in tool_spec.required_permissions
        ]
        allowed = all(decision.status in self._EXECUTABLE_STATUSES for decision in decisions)
        approval_reasons = self.approval_evaluator.approval_reasons(request, tool_spec)
        requires_approval = bool(approval_reasons)
        if allowed and requires_approval:
            reason = (
                "Permissions are granted but adaptive Runtime approval is required: "
                + ", ".join(approval_reasons)
            )
        elif allowed:
            reason = "All required permissions are granted"
        else:
            blocked = ", ".join(
                f"{decision.permission.value}={decision.status.value}"
                for decision in decisions
                if decision.status not in self._EXECUTABLE_STATUSES
            )
            reason = f"Non-executable permission status: {blocked}"
        return PermissionCheckResult(
            request_id=request.request_id,
            decisions=decisions,
            allowed=allowed,
            requires_approval=requires_approval,
            reason=reason,
        )

    @staticmethod
    def _deny_all(
        request: ToolCallRequest, tool_spec: ToolSpec, reason: str
    ) -> PermissionCheckResult:
        decisions = [
            PermissionDecision(
                request_id=request.request_id,
                permission=permission,
                status=PermissionStatus.DENIED,
                reason=reason,
            )
            for permission in tool_spec.required_permissions
        ]
        return PermissionCheckResult(
            request_id=request.request_id,
            decisions=decisions,
            allowed=False,
            requires_approval=False,
            reason=reason,
        )


class MockPermissionGate:
    """Phase 1 mock that marks trusted ToolSpec permissions as not required."""

    async def check(self, request: ToolCallRequest, tool_spec: ToolSpec) -> PermissionCheckResult:
        decisions = [
            PermissionDecision(
                request_id=request.request_id,
                permission=permission,
                status=PermissionStatus.NOT_REQUIRED,
                reason="Phase 1 mock permission decision",
            )
            for permission in tool_spec.required_permissions
        ]
        return PermissionCheckResult(
            request_id=request.request_id,
            decisions=decisions,
            allowed=True,
            requires_approval=False,
            reason="Phase 1 mock requires no real permission",
        )
