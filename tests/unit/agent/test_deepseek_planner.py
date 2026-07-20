from __future__ import annotations

import json

import httpx
import pytest

from ra_agent.agent.llm_client import DeepSeekClient
from ra_agent.agent.planner import DeepSeekPlanner, PlanningError


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
