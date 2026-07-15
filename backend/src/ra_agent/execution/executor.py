from typing import Protocol

from ra_agent.contracts import (
    ApprovalDecision,
    ExecutionStatus,
    ToolCallRequest,
    ToolExecutionResult,
)


class ToolExecutor(Protocol):
    async def execute(
        self,
        request: ToolCallRequest,
        *,
        checkpoint_id: str | None = None,
        approval_decision: ApprovalDecision | None = None,
    ) -> ToolExecutionResult: ...


class MockToolExecutor:
    """Returns mock output and never invokes a real tool."""

    async def execute(
        self,
        request: ToolCallRequest,
        *,
        checkpoint_id: str | None = None,
        approval_decision: ApprovalDecision | None = None,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=(
                ExecutionStatus.PENDING_COMMIT
                if checkpoint_id is not None
                else ExecutionStatus.SUCCESS
            ),
            checkpoint_id=checkpoint_id,
            output={"mock": True, "tool_name": request.tool_name},
        )
