import pytest
from fastapi.testclient import TestClient

from ra_agent.core.config import RuntimeMode, Settings
from ra_agent.main import create_app


def _grant(source: str, *, subject: str = "*", skill: str = "*") -> dict[str, object]:
    return {
        "subject": subject,
        "skill": skill,
        "tool": "create_file",
        "action": "create_file",
        "resource": "demo.txt",
        "effect": "ALLOW",
        "scope": "GLOBAL",
        "source": source,
    }


def test_confirmation_event_query_execute_and_replan_routes(tmp_path) -> None:
    app = create_app(
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            runtime_mode=RuntimeMode.OFFLINE,
            workspace_root=tmp_path / "workspace",
            core_event_log_path=tmp_path / "events.jsonl",
            core_memory_path=tmp_path / "memory.json",
            core_outbox_path=tmp_path / "outbox.jsonl",
        )
    )
    with TestClient(app) as client:
        created = client.post(
            "/api/v1/contracts",
            json={
                "session_id": "session-pr2-api",
                "task_id": "task-pr2-api",
                "user_id": "user-pr2-api",
                "goals": ["create a demo file"],
                "allowed": [
                    {
                        "tool": "create_file",
                        "action": "create_file",
                        "resource": "demo.txt",
                        "effect": "WRITE",
                    }
                ],
                "confirmation": {"required_actions": ["create_file"]},
                "policy_version": "policy:pr2-api",
                "tool_manifest_digest": "tools:pr2-api",
            },
        ).json()["data"]
        confirmed = client.post(
            f"/api/v1/contracts/{created['contract']['contract_id']}/confirm",
            json={"version": 1, "confirmed_by": "user-pr2-api"},
        ).json()["data"]
        body = {
            "envelope": {
                "request_id": "request-pr2-api",
                "task_id": "task-pr2-api",
                "session_id": "session-pr2-api",
                "contract_ref": confirmed["ref"],
                "skill_ref": "writer",
                "tool": "create_file",
                "action": "create_file",
                "canonical_args": {"path": "demo.txt", "content": "PR2 API"},
                "resource": "demo.txt",
                "effect_class": "WRITE",
            },
            "permissions": {
                "user_grants": [_grant("user", subject="user-pr2-api")],
                "skill_grants": [_grant("skill", skill="writer")],
                "system_grants": [_grant("system")],
            },
        }
        first = client.post("/api/v1/tool-calls/evaluate", json=body)
        assert first.status_code == 200
        first_data = first.json()["data"]
        assert first_data["decision"]["decision"] == "REQUIRE_CONFIRMATION"
        confirmation_id = first_data["decision"]["confirmation_id"]

        pending = client.get(f"/api/v1/confirmations/{confirmation_id}")
        assert pending.json()["data"]["status"] == "WAITING_CONFIRMATION"
        approved = client.post(
            f"/api/v1/confirmations/{confirmation_id}/resolve",
            json={"confirmed": True, "resolved_by": "user-pr2-api"},
        )
        assert approved.json()["data"]["decision"]["decision"] == "ALLOW"
        executed = client.post("/api/v1/tool-calls/request-pr2-api/execute")
        assert executed.status_code == 200
        assert (tmp_path / "workspace/demo.txt").read_text(encoding="utf-8") == "PR2 API"

        events = client.get("/api/v1/tasks/task-pr2-api/events")
        assert events.status_code == 200
        event_types = [row["type"] for row in events.json()["data"]]
        assert "CONTRACT_CREATED" in event_types
        assert "CONTRACT_CONFIRMED" in event_types
        assert "WAITING_CONFIRMATION" in event_types
        assert "CONFIRMED" in event_types
        assert "EXECUTION_FINISHED" in event_types

        replan = client.post(
            "/api/v1/tool-calls/request-pr2-api/replan",
            json={"changes": {"goals": ["replanned"]}},
        )
        assert replan.status_code == 200
        assert replan.json()["data"]["ref"]["status"] == "DRAFT"
        blocked = client.post("/api/v1/tool-calls/request-pr2-api/execute")
        assert blocked.status_code == 409


@pytest.mark.parametrize(
    ("resource", "existing_content", "expected_message"),
    [
        (
            "missing/demo.txt",
            None,
            "parent directory does not exist: missing/demo.txt",
        ),
        (
            "existing.txt",
            "original content",
            "create_file target already exists: existing.txt",
        ),
    ],
)
def test_execute_maps_expected_adapter_rejections_to_controlled_gateway_error(
    tmp_path,
    resource: str,
    existing_content: str | None,
    expected_message: str,
) -> None:
    workspace = tmp_path / "workspace"
    if existing_content is not None:
        workspace.mkdir(parents=True)
        (workspace / resource).write_text(existing_content, encoding="utf-8")
    app = create_app(
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            runtime_mode=RuntimeMode.OFFLINE,
            workspace_root=workspace,
            core_event_log_path=tmp_path / "events.jsonl",
            core_memory_path=tmp_path / "memory.json",
            core_outbox_path=tmp_path / "outbox.jsonl",
        )
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        created = client.post(
            "/api/v1/contracts",
            json={
                "session_id": "session-adapter-rejection",
                "task_id": "task-adapter-rejection",
                "user_id": "user-adapter-rejection",
                "goals": ["reject invalid file operations without side effects"],
                "allowed": [
                    {
                        "tool": "create_file",
                        "action": "create_file",
                        "resource": resource,
                        "effect": "WRITE",
                    }
                ],
                "policy_version": "policy:adapter-rejection",
                "tool_manifest_digest": "tools:adapter-rejection",
            },
        ).json()["data"]
        confirmed = client.post(
            f"/api/v1/contracts/{created['contract']['contract_id']}/confirm",
            json={"version": 1, "confirmed_by": "user-adapter-rejection"},
        ).json()["data"]

        def grant(source: str, *, subject: str = "*", skill: str = "*") -> dict[str, object]:
            return {
                "subject": subject,
                "skill": skill,
                "tool": "create_file",
                "action": "create_file",
                "resource": resource,
                "effect": "ALLOW",
                "scope": "GLOBAL",
                "source": source,
            }

        evaluated = client.post(
            "/api/v1/tool-calls/evaluate",
            json={
                "envelope": {
                    "request_id": "request-adapter-rejection",
                    "task_id": "task-adapter-rejection",
                    "session_id": "session-adapter-rejection",
                    "contract_ref": confirmed["ref"],
                    "skill_ref": "writer",
                    "tool": "create_file",
                    "action": "create_file",
                    "canonical_args": {
                        "path": resource,
                        "content": "must not be written",
                    },
                    "resource": resource,
                    "effect_class": "WRITE",
                },
                "permissions": {
                    "user_grants": [grant("user", subject="user-adapter-rejection")],
                    "skill_grants": [grant("skill", skill="writer")],
                    "system_grants": [grant("system")],
                },
            },
        )
        assert evaluated.status_code == 200
        assert evaluated.json()["data"]["decision"]["decision"] == "ALLOW"

        executed = client.post("/api/v1/tool-calls/request-adapter-rejection/execute")

        assert executed.status_code == 409
        assert executed.json()["detail"] == {
            "code": "TOOL_EXECUTION_REJECTED",
            "message": expected_message,
        }
        target = workspace / resource
        if existing_content is None:
            assert not target.exists()
        else:
            assert target.read_text(encoding="utf-8") == existing_content
        events = client.get("/api/v1/tasks/task-adapter-rejection/events").json()["data"]
        assert events[-1]["type"] == "EXECUTION_FAILED"
