"""Phase 3 checker boundaries; implementations remain member-owned."""

from typing import Protocol

from ra_agent.contracts import (
    PostCheckResult,
    PreCheckResult,
    RiskVerdict,
    ToolCallRequest,
    ToolExecutionResult,
)


class PreExecutionChecker(Protocol):
    async def check(
        self, request: ToolCallRequest, verdict: RiskVerdict
    ) -> PreCheckResult: ...


class PostExecutionChecker(Protocol):
    async def check(
        self, request: ToolCallRequest, execution: ToolExecutionResult
    ) -> PostCheckResult: ...


class MockPreExecutionChecker:
    async def check(
        self, request: ToolCallRequest, verdict: RiskVerdict
    ) -> PreCheckResult:
        return PreCheckResult(request_id=request.request_id, passed=True, reason="mock")


class MockPostExecutionChecker:
    async def check(
        self, request: ToolCallRequest, execution: ToolExecutionResult
    ) -> PostCheckResult:
        return PostCheckResult(request_id=request.request_id, passed=True, reason="mock")
