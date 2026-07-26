from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from ra_agent.agent.llm_client import DeepSeekClient
from ra_agent.agent.planner import DeepSeekPlanner, PlanningError
from ra_agent.contracts import ExecutionStatus, ToolExecutionResult


def _client(transport: httpx.MockTransport) -> DeepSeekClient:
    return DeepSeekClient(
        base_url="https://api.deepseek.example/v1",
        api_key="test-key",
        model="deepseek-chat",
        client=httpx.AsyncClient(transport=transport),
    )


@pytest.mark.asyncio
async def test_deepseek_client_posts_openai_compatible_request() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"tool_calls": []}'}}]},
        )

    client = _client(httpx.MockTransport(handler))
    response = await client.complete([{"role": "user", "content": "plan"}])
    await client.aclose()

    assert response == '{"tool_calls": []}'
    assert seen["url"] == "https://api.deepseek.example/v1/chat/completions"
    assert seen["authorization"] == "Bearer test-key"
    assert seen["body"] == {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "plan"}],
    }


@pytest.mark.asyncio
async def test_deepseek_client_prefers_json_schema_response_format() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"tool_calls": []}'}}]},
        )

    client = _client(httpx.MockTransport(handler))
    await client.complete_json([{"role": "user", "content": "plan"}])
    await client.aclose()

    assert seen["body"] == {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": "plan"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "aegis_tool_plan",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["tool_calls"],
                    "properties": {
                        "tool_calls": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["tool_name", "arguments", "context_summary"],
                                "properties": {
                                    "tool_name": {"type": "string"},
                                    "arguments": {"type": "object"},
                                    "context_summary": {"type": "string"},
                                },
                            },
                        }
                    },
                },
            },
        },
    }


@pytest.mark.asyncio
async def test_planner_owns_request_identity_and_rejects_unknown_tool() -> None:
    class StubClient:
        async def complete(self, messages: list[dict[str, str]]) -> str:
            return json.dumps(
                {
                    "tool_calls": [
                        {
                            "tool_name": "write_file",
                            "arguments": {"path": "note.txt", "content": "hello"},
                            "context_summary": "write the requested note",
                        }
                    ]
                }
            )

    planner = DeepSeekPlanner(StubClient(), allowed_tools={"write_file"})
    planned = await planner.plan("task-1", "write a note")

    assert planned[0].task_id == "task-1"
    assert planned[0].tool_name == "write_file"
    assert planned[0].request_id.startswith("request-")

    planner = DeepSeekPlanner(StubClient(), allowed_tools={"read_file"})
    with pytest.raises(PlanningError, match="not allowed"):
        await planner.plan("task-1", "write a note")


@pytest.mark.asyncio
async def test_iterative_planner_accepts_one_call_then_an_explicit_stop() -> None:
    class TurnClient:
        def __init__(self) -> None:
            self.responses = [
                json.dumps(
                    {
                        "tool_calls": [
                            {
                                "tool_name": "read_file",
                                "arguments": {"path": "note.txt"},
                                "context_summary": "inspect the note",
                            }
                        ]
                    }
                ),
                '{"tool_calls": []}',
            ]

        async def complete(self, messages: list[dict[str, str]]) -> str:
            return self.responses.pop(0)

    planner = DeepSeekPlanner(TurnClient(), allowed_tools={"read_file"})

    first = await planner.next_request("task-1", "inspect a note", [])
    second = await planner.next_request("task-1", "inspect a note", [])

    assert first is not None
    assert first.tool_name == "read_file"
    assert second is None


@pytest.mark.asyncio
async def test_iterative_planner_receives_bounded_redacted_result_data() -> None:
    class CapturingClient:
        messages: list[dict[str, str]]

        async def complete(self, messages: list[dict[str, str]]) -> str:
            self.messages = messages
            return '{"tool_calls": []}'

    client = CapturingClient()
    planner = DeepSeekPlanner(client, allowed_tools={"read_file"})
    result = ToolExecutionResult(
        task_id="task-1",
        step_id="step-1",
        request_id="request-1",
        status=ExecutionStatus.COMMITTED,
        output={"content": "useful result", "api_key": "hidden-value"},
        finished_at=datetime.now(UTC),
    )

    await planner.next_request("task-1", "inspect a note", [result])

    completed = json.loads(client.messages[1]["content"])["completed"]
    assert "useful result" in completed[0]["result"]
    assert "hidden-value" not in completed[0]["result"]
    assert "***REDACTED***" in completed[0]["result"]


@pytest.mark.asyncio
async def test_planner_tells_the_model_which_tools_are_allowed() -> None:
    class CapturingClient:
        messages: list[dict[str, str]]

        async def complete(self, messages: list[dict[str, str]]) -> str:
            self.messages = messages
            return '{"tool_calls": []}'

    client = CapturingClient()
    planner = DeepSeekPlanner(client, allowed_tools={"list_dir", "read_file"})

    await planner.plan("task-1", "inspect the workspace")

    assert '"list_dir"' in client.messages[0]["content"]
    assert '"read_file"' in client.messages[0]["content"]
    assert "Never return a bare tool call." in client.messages[0]["content"]
    assert 'For read_file, arguments must be exactly {"path": string}.' in client.messages[0]["content"]


@pytest.mark.asyncio
async def test_planner_rejects_read_file_arguments_outside_the_runtime_contract() -> None:
    class StubClient:
        async def complete(self, messages: list[dict[str, str]]) -> str:
            return json.dumps(
                {
                    "tool_calls": [
                        {
                            "tool_name": "read_file",
                            "arguments": {"file_path": "README.md"},
                            "context_summary": "read README",
                        }
                    ]
                }
            )

    planner = DeepSeekPlanner(StubClient(), allowed_tools={"read_file"})

    with pytest.raises(PlanningError, match="read_file arguments must be exactly"):
        await planner.plan("task-1", "read README")


@pytest.mark.asyncio
async def test_planner_retries_once_only_to_repair_invalid_json_format() -> None:
    class RepairingClient:
        def __init__(self) -> None:
            self.calls: list[list[dict[str, str]]] = []

        async def complete(self, messages: list[dict[str, str]]) -> str:
            self.calls.append(messages)
            if len(self.calls) == 1:
                return "I will read the file next."
            return '{"tool_calls": []}'

    client = RepairingClient()
    planner = DeepSeekPlanner(client, allowed_tools={"read_file"})

    assert await planner.plan("task-1", "inspect the workspace") == []
    assert len(client.calls) == 2
    assert client.calls[1][-1] == {
        "role": "user",
        "content": (
            "Repair only the JSON format. Return exactly one object with a top-level "
            "tool_calls array, never a bare tool call, and nothing else."
        ),
    }


@pytest.mark.asyncio
async def test_planner_rejects_two_invalid_json_responses_without_guessing_calls() -> None:
    class InvalidClient:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, messages: list[dict[str, str]]) -> str:
            self.calls += 1
            return "call read_file with path secret.txt"

    client = InvalidClient()
    planner = DeepSeekPlanner(client, allowed_tools={"read_file"})

    with pytest.raises(PlanningError, match="model response must contain JSON tool_calls"):
        await planner.plan("task-1", "inspect the workspace")

    assert client.calls == 2
