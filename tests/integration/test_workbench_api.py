import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ra_agent.agent import AgentRuntime
from ra_agent.agent.planner import PlannerDecision
from ra_agent.contracts import SourceType, TaskContract, ToolCallRequest
from ra_agent.core.config import RuntimeMode, Settings
from ra_agent.main import create_app
from ra_agent.security import RuleBasedIntentBoundaryGuard


class FinalPlanner:
    async def next_action(self, task_id, objective, results):
        del task_id, results
        return PlannerDecision(final_answer=f"Completed: {objective}")


class CapturingContextPlanner(FinalPlanner):
    def __init__(self) -> None:
        self.contexts: list[list[dict[str, str]]] = []

    async def next_action_with_context(
        self,
        task_id,
        objective,
        results,
        context_messages,
    ):
        self.contexts.append(context_messages)
        return await self.next_action(task_id, objective, results)


class CreateFilePlanner:
    async def next_action(self, task_id, objective, results):
        if results:
            return PlannerDecision(final_answer="Created conversation-proof.md safely.")
        return PlannerDecision(
            tool_call=ToolCallRequest(
                task_id=task_id,
                step_id="step-conversation-write",
                request_id=f"request-{task_id}",
                tool_name="create_file",
                arguments={
                    "path": "conversation-proof.md",
                    "content": "runtime proof\n",
                },
                objective=objective,
                context_summary="Create the user-requested proof file",
                source_type=SourceType.AGENT,
                requested_at=datetime.now(UTC),
            )
        )


def test_security_profile_versions_and_conversation_task_snapshots() -> None:
    app = create_app(Settings(runtime_mode=RuntimeMode.OFFLINE))
    with TestClient(app) as client:
        app.state.agent_runner = AgentRuntime(
            planner=FinalPlanner(),
            scheduler=app.state.agent_runner.scheduler,
        )
        first = client.get("/api/security-profiles/default").json()["data"]
        assert first["version"] == 1
        assert first["resource_scopes"] == ["**"]
        assert "delete_file" in first["denied_actions"]

        update = {
            "allowed_actions": [*first["allowed_actions"], "delete_file"],
            "resource_scopes": ["**"],
            "allow_egress": False,
            "max_affected_objects": 100,
            "approval_policy": {
                "required_actions": ["delete_file"],
                "bulk_action_threshold": 20,
            },
        }
        second = client.put("/api/security-profiles/default", json=update).json()[
            "data"
        ]
        assert second["version"] == 2
        assert "delete_file" not in second["denied_actions"]

        conversation = client.post(
            "/api/conversations",
            json={"title": "continuity", "security_profile_id": "default"},
        ).json()["data"]
        turn = client.post(
            f"/api/conversations/{conversation['conversation_id']}/messages",
            json={"content": "Remember the report and answer."},
        ).json()["data"]

        task = None
        for _ in range(50):
            task = client.get(f"/api/tasks/{turn['task_id']}").json()["data"]
            if task["status"] == "COMPLETED":
                break
            time.sleep(0.01)
        assert task is not None
        assert task["status"] == "COMPLETED"
        assert task["security_profile_id"] == "default"
        assert task["security_profile_version"] == 2
        assert task["contract"]["security_profile_version"] == 2

        restored = client.get(
            f"/api/conversations/{conversation['conversation_id']}"
        ).json()["data"]
        assert [item["role"] for item in restored["messages"]] == ["user", "assistant"]
        assert all(item["task_id"] == turn["task_id"] for item in restored["messages"])


def test_profile_rejects_a_scope_outside_the_workspace() -> None:
    app = create_app(Settings(runtime_mode=RuntimeMode.OFFLINE))
    with TestClient(app) as client:
        profile = client.get("/api/security-profiles/default").json()["data"]
        response = client.put(
            "/api/security-profiles/default",
            json={
                "allowed_actions": profile["allowed_actions"],
                "resource_scopes": ["../secrets/**"],
                "allow_egress": False,
                "max_affected_objects": 10,
                "approval_policy": {
                    "required_actions": [],
                    "bulk_action_threshold": 5,
                },
            },
        )
        assert response.status_code == 422


def test_follow_up_turn_receives_prior_user_and_assistant_messages() -> None:
    planner = CapturingContextPlanner()
    app = create_app(Settings(runtime_mode=RuntimeMode.OFFLINE))
    with TestClient(app) as client:
        app.state.agent_runner = AgentRuntime(
            planner=planner,
            scheduler=app.state.agent_runner.scheduler,
        )
        conversation = client.post("/api/conversations", json={}).json()["data"]
        endpoint = f"/api/conversations/{conversation['conversation_id']}/messages"
        first = client.post(endpoint, json={"content": "Create the report."}).json()[
            "data"
        ]
        for _ in range(50):
            if (
                client.get(f"/api/tasks/{first['task_id']}").json()["data"]["status"]
                == "COMPLETED"
            ):
                break
            time.sleep(0.01)
        second = client.post(endpoint, json={"content": "Now make it shorter."}).json()[
            "data"
        ]
        for _ in range(50):
            if (
                client.get(f"/api/tasks/{second['task_id']}").json()["data"]["status"]
                == "COMPLETED"
            ):
                break
            time.sleep(0.01)

    assert planner.contexts
    contents = [item["content"] for item in planner.contexts[-1]]
    assert "Create the report." in contents
    assert "Completed: Create the report." in contents


def test_profile_and_conversation_survive_a_persistent_runtime_restart(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    settings = Settings(
        runtime_mode=RuntimeMode.RULES_ONLY,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}",
        security_config_dir=root / "configs",
        workspace_root=tmp_path / "workspace",
        pending_root=tmp_path / "pending",
        checkpoint_root=tmp_path / "checkpoints",
        quarantine_root=tmp_path / "quarantine",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        first = client.get("/api/security-profiles/default").json()["data"]
        update = {
            "allowed_actions": first["allowed_actions"],
            "resource_scopes": ["reports/**"],
            "allow_egress": False,
            "max_affected_objects": 25,
            "approval_policy": {
                "required_actions": [],
                "bulk_action_threshold": 5,
            },
        }
        saved = client.put("/api/security-profiles/default", json=update).json()["data"]
        created = client.post(
            "/api/conversations",
            json={"title": "persistent conversation", "security_profile_id": "default"},
        ).json()["data"]

    restarted = create_app(settings)
    with TestClient(restarted) as client:
        restored_profile = client.get("/api/security-profiles/default").json()["data"]
        restored_conversation = client.get(
            f"/api/conversations/{created['conversation_id']}"
        ).json()["data"]
        assert restored_profile["version"] == saved["version"]
        assert restored_profile["resource_scopes"] == ["reports/**"]
        assert restored_conversation["title"] == "persistent conversation"


@pytest.mark.asyncio
async def test_legacy_reconfirmation_flag_no_longer_blocks_an_authorized_mutation() -> (
    None
):
    contract = TaskContract(
        allowed_actions=["create_file"],
        allowed_resources=["report.md"],
        forbidden_actions=[],
        max_affected_objects=1,
        allow_egress=False,
        requires_reconfirmation=True,
    )
    request = ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id="request-1",
        tool_name="create_file",
        arguments={"path": "report.md", "content": "safe"},
        objective="Create report.md",
        context_summary="User asked for a workspace report",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
        task_contract=contract,
    )
    result = await RuleBasedIntentBoundaryGuard().check(request)
    assert result.allowed is True


def test_conversation_agent_write_uses_the_real_runtime_chain(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    settings = Settings(
        runtime_mode=RuntimeMode.LIVE_AGENT,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}",
        security_config_dir=root / "configs",
        workspace_root=tmp_path / "workspace",
        pending_root=tmp_path / "pending",
        checkpoint_root=tmp_path / "checkpoints",
        quarantine_root=tmp_path / "quarantine",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.agent_runner = AgentRuntime(
            planner=CreateFilePlanner(),
            scheduler=app.state.agent_runner.scheduler,
        )
        conversation = client.post("/api/conversations", json={}).json()["data"]
        turn = client.post(
            f"/api/conversations/{conversation['conversation_id']}/messages",
            json={"content": "Create conversation-proof.md."},
        ).json()["data"]
        task = None
        for _ in range(100):
            task = client.get(f"/api/tasks/{turn['task_id']}").json()["data"]
            if task["status"] in {"COMPLETED", "BLOCKED", "FAILED"}:
                break
            time.sleep(0.01)
        events = client.get(f"/api/tasks/{turn['task_id']}/events").json()["data"][
            "events"
        ]

    assert task is not None and task["status"] == "COMPLETED"
    assert (
        settings.workspace_root / "conversation-proof.md"
    ).read_text() == "runtime proof\n"
    event_types = {item["event_type"] for item in events}
    assert "CHECKPOINT_CREATED" in event_types
    assert "DEEP_CHECK_FINISHED" in event_types
    assert "COMMIT_FINISHED" in event_types


def test_profile_approval_rule_waits_then_resumes_the_same_conversation_task(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    settings = Settings(
        runtime_mode=RuntimeMode.LIVE_AGENT,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}",
        security_config_dir=root / "configs",
        workspace_root=tmp_path / "workspace",
        pending_root=tmp_path / "pending",
        checkpoint_root=tmp_path / "checkpoints",
        quarantine_root=tmp_path / "quarantine",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.agent_runner = AgentRuntime(
            planner=CreateFilePlanner(),
            scheduler=app.state.agent_runner.scheduler,
        )
        profile = client.get("/api/security-profiles/default").json()["data"]
        client.put(
            "/api/security-profiles/default",
            json={
                "allowed_actions": profile["allowed_actions"],
                "resource_scopes": ["**"],
                "allow_egress": False,
                "max_affected_objects": 100,
                "approval_policy": {
                    "required_actions": ["create_file"],
                    "bulk_action_threshold": 20,
                },
            },
        )
        conversation = client.post("/api/conversations", json={}).json()["data"]
        turn = client.post(
            f"/api/conversations/{conversation['conversation_id']}/messages",
            json={"content": "Create conversation-proof.md after approval."},
        ).json()["data"]
        task = None
        for _ in range(100):
            task = client.get(f"/api/tasks/{turn['task_id']}").json()["data"]
            if task["status"] == "WAITING_APPROVAL":
                break
            time.sleep(0.01)
        assert task is not None and task["status"] == "WAITING_APPROVAL"
        assert not (settings.workspace_root / "conversation-proof.md").exists()
        approvals = client.get(f"/api/tasks/{turn['task_id']}/approvals").json()["data"]
        assert len(approvals) == 1
        client.post(
            f"/api/approvals/{approvals[0]['approval_id']}/grant?decided_by=tester"
        ).raise_for_status()
        for _ in range(100):
            task = client.get(f"/api/tasks/{turn['task_id']}").json()["data"]
            if task["status"] == "COMPLETED":
                break
            time.sleep(0.01)

    assert task is not None and task["status"] == "COMPLETED"
    assert (settings.workspace_root / "conversation-proof.md").exists()
