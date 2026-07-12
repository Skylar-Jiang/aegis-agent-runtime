from typing import Protocol

from ra_agent.contracts import ExecutionStatus, RollbackResult


class RollbackManager(Protocol):
    async def rollback(self, checkpoint_id: str, request_id: str) -> RollbackResult: ...


class MockRollbackManager:
    """No-side-effect rollback mock for orchestration tests."""

    async def rollback(self, checkpoint_id: str, request_id: str) -> RollbackResult:
        return RollbackResult(
            request_id=request_id,
            checkpoint_id=checkpoint_id,
            status=ExecutionStatus.ROLLED_BACK,
            reason="mock rollback completed",
        )
