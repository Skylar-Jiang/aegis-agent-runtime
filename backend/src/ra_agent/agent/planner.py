from typing import Protocol

from ra_agent.contracts import ToolCallRequest


class Planner(Protocol):
    async def plan(self, task_id: str, objective: str) -> list[ToolCallRequest]: ...


class MockPlanner:
    """Returns fixed requests and never calls an LLM or executes a tool."""

    def __init__(self, requests: list[ToolCallRequest]) -> None:
        self._requests = tuple(requests)

    async def plan(self, task_id: str, objective: str) -> list[ToolCallRequest]:
        return list(self._requests)
