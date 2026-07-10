from typing import Protocol

from ra_agent.contracts import RollbackResult


class RollbackManager(Protocol):
    async def rollback(self, checkpoint_id: str, request_id: str) -> RollbackResult: ...
