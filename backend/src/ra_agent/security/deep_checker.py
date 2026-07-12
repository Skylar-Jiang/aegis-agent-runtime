from typing import Protocol

from ra_agent.contracts import DeepCheckResult, ToolCallRequest, ToolExecutionResult


class DeepSafetyChecker(Protocol):
    async def check(
        self, request: ToolCallRequest, result: ToolExecutionResult
    ) -> DeepCheckResult: ...


class MockDeepSafetyChecker:
    """Phase 1 mock; it must never be presented as a real deep checker."""

    def __init__(self, *, passed: bool = True, reason: str = "mock deep-check result") -> None:
        self.passed = passed
        self.reason = reason

    async def check(self, request: ToolCallRequest, result: ToolExecutionResult) -> DeepCheckResult:
        return DeepCheckResult(
            request_id=request.request_id,
            passed=self.passed,
            reason=self.reason,
        )
