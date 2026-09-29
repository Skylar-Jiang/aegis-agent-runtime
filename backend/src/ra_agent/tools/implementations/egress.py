from ra_agent.contracts import (
    DataLineage,
    ExecutionStatus,
    PendingEgress,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution.artifacts import build_tool_output_artifact


class SendEmailDryRunHandler:
    """Create an auditable pending egress record without opening a network connection."""

    async def __call__(self, request: ToolCallRequest) -> ToolExecutionResult:
        artifact = DataLineage.model_validate(request.arguments["artifact"])
        pending = PendingEgress(artifact=artifact, recipient=str(request.arguments["recipient"]))
        output = pending.model_dump(mode="json")
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.SUCCESS,
            output=output,
            artifacts=[
                build_tool_output_artifact(
                    request,
                    output,
                    status=ExecutionStatus.SUCCESS,
                )
            ],
        )
