from typing import Protocol

from ra_agent.contracts import CommitResult, DeepCheckResult, ExecutionStatus, ToolExecutionResult


class CommitGate(Protocol):
    async def commit(
        self, execution: ToolExecutionResult, deep_check: DeepCheckResult
    ) -> CommitResult: ...


class MockCommitGate:
    """No-side-effect commit mock for orchestration tests."""

    async def commit(
        self, execution: ToolExecutionResult, deep_check: DeepCheckResult
    ) -> CommitResult:
        return CommitResult(
            request_id=execution.request_id,
            checkpoint_id=execution.checkpoint_id,
            status=ExecutionStatus.COMMITTED,
        )
