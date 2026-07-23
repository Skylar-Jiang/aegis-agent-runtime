"""Fail-closed validation of user-approved task boundaries."""

from fnmatch import fnmatchcase
from typing import Protocol

from ra_agent.contracts import IntentBoundaryResult, ToolCallRequest


class IntentBoundaryGuard(Protocol):
    async def check(self, request: ToolCallRequest) -> IntentBoundaryResult: ...


class RuleBasedIntentBoundaryGuard:
    """Reject calls outside a TaskContract before a Runtime flow selects an executor."""

    _RESOURCE_ARGUMENTS = frozenset(
        {"path", "paths", "destination", "destination_path", "target_path", "key"}
    )
    _EGRESS_TOOLS = frozenset({"send_email_dry_run", "export_data"})
    _MUTATING_TOOLS = frozenset(
        {"write_file", "delete_file", "download_url", "memory_write"} | _EGRESS_TOOLS
    )

    async def check(self, request: ToolCallRequest) -> IntentBoundaryResult:
        contract = request.task_contract
        if contract is None:
            return self._blocked("TaskContract is required in live runtime", "contract_missing")

        signals: list[str] = []
        if request.tool_name in contract.forbidden_actions:
            signals.append("action_forbidden")
        if request.tool_name not in contract.allowed_actions:
            signals.append("action_not_allowed")
        if request.tool_name in self._EGRESS_TOOLS and not contract.allow_egress:
            signals.append("egress_not_allowed")
        if (
            contract.requires_reconfirmation
            and request.tool_name in self._MUTATING_TOOLS
            and request.arguments.get("reconfirmed") is not True
        ):
            signals.append("reconfirmation_required")

        resources = self._resources(request)
        if len(resources) > contract.max_affected_objects:
            signals.append("affected_object_limit_exceeded")
        if any(
            not any(fnmatchcase(resource, pattern) for pattern in contract.allowed_resources)
            for resource in resources
        ):
            signals.append("resource_not_allowed")
        if signals:
            return self._blocked("TaskContract rejected the requested action", *signals)
        return IntentBoundaryResult(
            allowed=True,
            reason="Tool request is within the TaskContract boundary",
        )

    @classmethod
    def _resources(cls, request: ToolCallRequest) -> list[str]:
        resources: list[str] = []
        for key, value in request.arguments.items():
            if key not in cls._RESOURCE_ARGUMENTS:
                continue
            if isinstance(value, str):
                resources.append(value.removeprefix("./"))
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                resources.extend(item.removeprefix("./") for item in value)
        return resources

    @staticmethod
    def _blocked(reason: str, *signals: str) -> IntentBoundaryResult:
        return IntentBoundaryResult(allowed=False, reason=reason, signals=list(signals))
