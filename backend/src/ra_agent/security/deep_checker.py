from typing import Protocol

from ra_agent.contracts import DeepCheckResult, ToolCallRequest, ToolExecutionResult


class DeepSafetyChecker(Protocol):
    async def check(
        self, request: ToolCallRequest, result: ToolExecutionResult
    ) -> DeepCheckResult: ...


class MockDeepSafetyChecker:
    """Phase 0 mock; it must never be presented as a real deep checker."""

    async def check(self, request: ToolCallRequest, result: ToolExecutionResult) -> DeepCheckResult:
        return DeepCheckResult(
            request_id=request.request_id,
            passed=True,
            reason="Phase 0 mock deep-check result",
        )
