from __future__ import annotations

from fastapi.testclient import TestClient

from ra_agent.agent.state import AgentRunStatus, AgentState
from ra_agent.core.config import Settings
from ra_agent.main import create_app


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def run(self, task_id: str, objective: str, contract: object = None) -> AgentState:
        self.calls.append((task_id, objective))
        return AgentState(task_id=task_id, objective=objective, status=AgentRunStatus.COMPLETED)


def test_create_task_runs_agent_and_returns_its_status() -> None:
    app = create_app(Settings.model_validate({"runtime_mode": "offline"}))
    runner = RecordingRunner()
    app.state.agent_runner = runner

    response = TestClient(app).post("/api/tasks", json={"objective": "inspect the workspace"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "COMPLETED"
    assert runner.calls == [(data["task_id"], "inspect the workspace")]
