from typing import Protocol

from ra_agent.contracts import ExecutionStatus, ToolCallRequest, ToolExecutionResult


class ToolExecutor(Protocol):
    async def execute(self, request: ToolCallRequest) -> ToolExecutionResult: ...


class MockToolExecutor:
    """Returns mock output and never invokes a real tool."""

    async def execute(self, request: ToolCallRequest) -> ToolExecutionResult:
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.SUCCESS,
            output={"mock": True, "tool_name": request.tool_name},
        )
