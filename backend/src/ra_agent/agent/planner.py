import json
from datetime import UTC, datetime
from typing import Protocol

from ra_agent.contracts import SourceType, ToolCallRequest
from ra_agent.core.ids import new_id

from .llm_client import LLMClient


class Planner(Protocol):
    async def plan(self, task_id: str, objective: str) -> list[ToolCallRequest]: ...


class MockPlanner:
    """Returns fixed requests and never calls an LLM or executes a tool."""

    def __init__(self, requests: list[ToolCallRequest]) -> None:
        self._requests = tuple(requests)

    async def plan(self, task_id: str, objective: str) -> list[ToolCallRequest]:
        return list(self._requests)


class PlanningError(ValueError):
    """Raised when an untrusted model response cannot become a tool request."""


class DeepSeekPlanner:
    """Turn bounded model JSON into Runtime-owned ToolCallRequest values."""

    def __init__(self, client: LLMClient, *, allowed_tools: set[str]) -> None:
        self._client = client
        self._allowed_tools = frozenset(allowed_tools)

    async def plan(self, task_id: str, objective: str) -> list[ToolCallRequest]:
        content = await self._client.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "Return JSON only: {\"tool_calls\":[{\"tool_name\":str,"
                        "\"arguments\":object,\"context_summary\":str}]}."
                    ),
                },
                {"role": "user", "content": objective},
            ]
        )
        try:
            payload = json.loads(content)
            raw_calls = payload["tool_calls"]
        except (TypeError, KeyError, json.JSONDecodeError) as error:
            raise PlanningError("model response must contain JSON tool_calls") from error
        if not isinstance(raw_calls, list):
            raise PlanningError("tool_calls must be a list")
        return [
            self._request(task_id, objective, index, raw)
            for index, raw in enumerate(raw_calls)
        ]

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()

    def _request(
        self, task_id: str, objective: str, index: int, raw: object
    ) -> ToolCallRequest:
        if not isinstance(raw, dict) or set(raw) != {
            "tool_name",
            "arguments",
            "context_summary",
        }:
            raise PlanningError("each tool call must use the supported schema")
        tool_name = raw["tool_name"]
        arguments = raw["arguments"]
        context_summary = raw["context_summary"]
        if not isinstance(tool_name, str) or tool_name not in self._allowed_tools:
            raise PlanningError("planned tool is not allowed")
        if not isinstance(arguments, dict) or not isinstance(context_summary, str):
            raise PlanningError("tool arguments and context_summary must be valid")
        return ToolCallRequest(
            task_id=task_id,
            step_id=f"step-{index + 1}-{new_id('plan')}",
            request_id=new_id("request"),
            tool_name=tool_name,
            arguments=arguments,
            objective=objective,
            context_summary=context_summary,
            source_type=SourceType.AGENT,
            requested_at=datetime.now(UTC),
        )


class UnavailablePlanner:
    """Fail a run safely when live planning is intentionally unavailable."""

    def __init__(self, reason: str) -> None:
        self._reason = reason

    async def plan(self, task_id: str, objective: str) -> list[ToolCallRequest]:
        raise PlanningError(self._reason)
