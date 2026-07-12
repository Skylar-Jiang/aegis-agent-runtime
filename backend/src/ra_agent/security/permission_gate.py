from typing import Protocol

from ra_agent.contracts import (
    PermissionCheckResult,
    PermissionDecision,
    PermissionStatus,
    ToolCallRequest,
    ToolSpec,
)


class PermissionGate(Protocol):
    async def check(
        self, request: ToolCallRequest, tool_spec: ToolSpec
    ) -> PermissionCheckResult: ...


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
