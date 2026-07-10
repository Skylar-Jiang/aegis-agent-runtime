from typing import Protocol

from ra_agent.contracts import CommitResult, DeepCheckResult, ToolExecutionResult


class CommitGate(Protocol):
    async def commit(
        self, execution: ToolExecutionResult, deep_check: DeepCheckResult
    ) -> CommitResult: ...
