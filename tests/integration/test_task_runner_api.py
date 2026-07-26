from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from ra_agent.agent.state import AgentRunStatus, AgentState
from ra_agent.contracts import ApprovalRequest
from ra_agent.core.config import Settings
from ra_agent.main import create_app


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def run(self, task_id: str, objective: str, contract: object = None) -> AgentState:
        self.calls.append((task_id, objective))
        return AgentState(task_id=task_id, objective=objective, status=AgentRunStatus.COMPLETED)


class BlockingRunner:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    async def run(self, task_id: str, objective: str, contract: object = None) -> AgentState:
        self.started.set()
        await asyncio.to_thread(self.release.wait)
        return AgentState(task_id=task_id, objective=objective, status=AgentRunStatus.COMPLETED)


class CancellableRunner:
    def __init__(self) -> None:
        self.cancelled_task_ids: list[str] = []

    async def run(self, task_id: str, objective: str, contract: object = None) -> AgentState:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def cancel_task(self, task_id: str) -> None:
        self.cancelled_task_ids.append(task_id)


def test_create_task_returns_running_before_background_agent_completes() -> None:
    app = create_app(Settings.model_validate({"runtime_mode": "offline"}))
    runner = BlockingRunner()
    app.state.agent_runner = runner

    with TestClient(app) as client:
        response = client.post("/api/tasks", json={"objective": "inspect the workspace"})
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "RUNNING"
        assert runner.started.wait(timeout=1)
        assert client.get(f"/api/tasks/{data['task_id']}").json()["data"]["status"] == "RUNNING"

        runner.release.set()
        for _ in range(20):
            if client.get(f"/api/tasks/{data['task_id']}").json()["data"]["status"] == "COMPLETED":
                break
            time.sleep(0.01)
        assert client.get(f"/api/tasks/{data['task_id']}").json()["data"]["status"] == "COMPLETED"


def test_cancel_is_idempotent_and_unknown_task_is_not_found() -> None:
    app = create_app(Settings.model_validate({"runtime_mode": "offline"}))
    runner = CancellableRunner()
    app.state.agent_runner = runner

    with TestClient(app) as client:
        assert client.post("/api/tasks/missing/cancel").status_code == 404
        task_id = client.post("/api/tasks", json={"objective": "wait"}).json()["data"]["task_id"]
        first = client.post(f"/api/tasks/{task_id}/cancel")
        second = client.post(f"/api/tasks/{task_id}/cancel")
        assert first.json()["data"]["status"] == "CANCELLED"
        assert second.json()["data"]["status"] == "CANCELLED"
        assert runner.cancelled_task_ids == [task_id, task_id]


def test_task_approvals_are_queryable_by_task() -> None:
    app = create_app(Settings.model_validate({"runtime_mode": "offline"}))
    app.state.agent_runner = CancellableRunner()
    now = datetime.now(UTC)

    with TestClient(app) as client:
        task_id = client.post("/api/tasks", json={"objective": "wait"}).json()["data"]["task_id"]
        approval = ApprovalRequest(
            approval_id="approval-for-task",
            task_id=task_id,
            step_id="step-1",
            request_id="request-1",
            tool_name="delete_file",
            request_fingerprint="fingerprint",
            reason="destructive action",
            requested_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        asyncio.run(app.state.services.approval_service.create(approval))

        response = client.get(f"/api/tasks/{task_id}/approvals")

        assert response.status_code == 200
        assert response.json()["data"] == [
            {
                "approval_id": "approval-for-task",
                "status": "PENDING",
                "tool_name": "delete_file",
                "reason": "destructive action",
                "step_id": "step-1",
                "request_id": "request-1",
            }
        ]
