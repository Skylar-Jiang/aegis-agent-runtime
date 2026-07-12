from typing import Protocol

from ra_agent.contracts import CheckpointResult, ExecutionStatus, ToolCallRequest


class CheckpointManager(Protocol):
    async def create(self, request: ToolCallRequest) -> CheckpointResult: ...


class MockCheckpointManager:
    """No-side-effect checkpoint mock for orchestration tests."""

    async def create(self, request: ToolCallRequest) -> CheckpointResult:
        return CheckpointResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            checkpoint_id=f"checkpoint-{request.request_id}",
            status=ExecutionStatus.SUCCESS,
        )
