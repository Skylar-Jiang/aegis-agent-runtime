from typing import Protocol

from ra_agent.contracts import (
    PermissionDecision,
    PermissionStatus,
    PermissionType,
    ToolCallRequest,
)


class PermissionGate(Protocol):
    async def check(self, request: ToolCallRequest) -> PermissionDecision: ...


class MockPermissionGate:
    """Phase 0 mock that marks permission as not required."""

    async def check(self, request: ToolCallRequest) -> PermissionDecision:
        return PermissionDecision(
            request_id=request.request_id,
            permission=PermissionType.FILE_LIST,
            status=PermissionStatus.NOT_REQUIRED,
            reason="Phase 0 mock permission decision",
        )
