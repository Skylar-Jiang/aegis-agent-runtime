from ra_agent.contracts import (
    PermissionCheckResult,
    PermissionStatus,
    ToolCallRequest,
    ToolSpec,
)

from .correlation import CorrelationError


def permission_failure_reason(
    request: ToolCallRequest,
    tool_spec: ToolSpec,
    permission: PermissionCheckResult,
    *,
    approval_satisfied: bool = False,
) -> str | None:
    if permission.request_id != request.request_id:
        raise CorrelationError(
            "PermissionCheckResult.request_id does not match the current request"
        )
    if any(item.request_id != request.request_id for item in permission.decisions):
        raise CorrelationError("PermissionDecision.request_id does not match the current request")

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
    if permission.requires_approval and not approval_satisfied:
        return "Permission result requires approval and cannot use FAST_EXECUTE"

    executable_statuses = {PermissionStatus.GRANTED, PermissionStatus.NOT_REQUIRED}
    decisions_allow = all(item.status in executable_statuses for item in permission.decisions)
    if permission.allowed != decisions_allow:
        return "Permission result allowed flag contradicts its decisions"
    if not permission.allowed:
        return permission.reason or "Permission result does not allow execution"
    return None
