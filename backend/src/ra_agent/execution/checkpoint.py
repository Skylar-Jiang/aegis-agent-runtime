from typing import Protocol

from ra_agent.contracts import CheckpointResult, ToolCallRequest


class CheckpointManager(Protocol):
    async def create(self, request: ToolCallRequest) -> CheckpointResult: ...
