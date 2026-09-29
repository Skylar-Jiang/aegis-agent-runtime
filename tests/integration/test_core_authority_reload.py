"""Running HTTP requests must observe tightened trusted configuration on disk."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from ra_agent.core.config import RuntimeMode, Settings
from ra_agent.main import create_app


@pytest.fixture
def isolated_app(tmp_path):
    config = tmp_path / "configs"
    shutil.copytree(Path(__file__).resolve().parents[2] / "configs", config)
    (tmp_path / "workspace/reports").mkdir(parents=True)
    app = create_app(
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            runtime_mode=RuntimeMode.OFFLINE,
            security_config_dir=config,
            workspace_root=tmp_path / "workspace",
            core_event_log_path=tmp_path / "events.jsonl",
            core_memory_path=tmp_path / "memory.json",
            core_outbox_path=tmp_path / "outbox.jsonl",
        )
    )
    return app, config


def _evaluate_write(client):
    created_response = client.post(
        "/api/v1/contracts",
        json={
            "session_id": "reload-session",
            "task_id": "reload-task",
            "user_id": "reload-user",
            "goals": ["Write an authorized report"],
            "allowed": [
                {
                    "tool": "write_file",
                    "action": "write_file",
                    "resource": "reports/**",
                    "effect": "WRITE",
                }
            ],
            "policy_version": "reload:1",
            "tool_manifest_digest": "reload-manifest",
        },
    )
    assert created_response.status_code == 200
    created = created_response.json()["data"]
    confirmed_response = client.post(
        f"/api/v1/contracts/{created['contract']['contract_id']}/confirm",
        json={"version": 1, "confirmed_by": "reload-user"},
    )
    assert confirmed_response.status_code == 200
    confirmed = confirmed_response.json()["data"]
    # These broad client grants stay unchanged during both tests. They must never
    # override a subsequently tightened system policy or registered Skill ceiling.
    client_grant = {
        "subject": "*",
        "skill": "*",
        "tool": "*",
        "action": "*",
        "resource": "*",
        "effect": "ALLOW",
        "scope": "GLOBAL",
        "source": "client",
    }
    response = client.post(
        "/api/v1/tool-calls/evaluate",
        json={
            "envelope": {
                "request_id": "reload-request",
                "session_id": "reload-session",
                "task_id": "reload-task",
                "contract_ref": confirmed["ref"],
                "skill_ref": "writer",
                "tool": "write_file",
                "action": "write_file",
                "effect_class": "WRITE",
                "resource": "reports/allowed.txt",
                "canonical_args": {"path": "reports/allowed.txt", "content": "authorized"},
            },
            "permissions": {
                layer: [client_grant] for layer in ("user_grants", "skill_grants", "system_grants")
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["decision"]["decision"] == "ALLOW"


def _assert_execute_denied(client, tmp_path, reason):
    response = client.post("/api/v1/tool-calls/reload-request/execute")
    assert response.status_code == 409
    assert reason in response.json()["detail"]
    assert not (tmp_path / "workspace/reports/allowed.txt").exists()
    events = client.get("/api/v1/tasks/reload-task/events").json()["data"]
    assert events[-1]["type"] == "GATEWAY_DENIED"
    assert events[-1]["reason_code"] == reason
    assert not any(item["type"] == "EXECUTION_STARTED" for item in events)


def test_running_http_gateway_observes_system_permission_revocation(isolated_app, tmp_path):
    app, config = isolated_app
    permissions_path = config / "permissions.yaml"
    rules = yaml.safe_load(permissions_path.read_text(encoding="utf-8"))
    assert rules["permission_statuses"]["write_file"]["FILE_WRITE"] == "GRANTED"
    with TestClient(app) as client:
        _evaluate_write(client)
        rules["permission_statuses"]["write_file"]["FILE_WRITE"] = "DENIED"
        permissions_path.write_text(yaml.safe_dump(rules), encoding="utf-8")
        _assert_execute_denied(client, tmp_path, "SYSTEM_DENY")


def test_running_http_gateway_observes_skill_manifest_tightening(isolated_app, tmp_path):
    app, config = isolated_app
    manifest_path = config / "core_skills.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "write_file" in manifest["skills"]["writer"]
    with TestClient(app) as client:
        _evaluate_write(client)
        manifest["skills"]["writer"].remove("write_file")
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        _assert_execute_denied(client, tmp_path, "SKILL_LIMIT")
