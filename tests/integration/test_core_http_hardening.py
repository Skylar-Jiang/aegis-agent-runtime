"""HTTP callers cannot mint their own tool permissions or redefine side effects."""

import asyncio
import os
import subprocess
from copy import deepcopy

from fastapi.testclient import TestClient

from ra_agent.core.config import RuntimeMode, Settings
from ra_agent.database.workbench_store import DEFAULT_PROFILE
from ra_agent.main import create_app


def test_audit_verification_does_not_run_on_the_http_event_loop(tmp_path):
    from ra_agent.audit import VerificationResult

    class Verifier:
        on_event_loop = None

        def verify_bundle(self, *args, **kwargs):
            try:
                asyncio.get_running_loop()
                self.on_event_loop = True
            except RuntimeError:
                self.on_event_loop = False
            return VerificationResult(valid=True)

    app = _app(tmp_path)
    verifier = Verifier()
    app.state.core_audit_verifier = verifier
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/audit/verify",
            json={
                "bundle": {},
                "trusted_checkpoint_id": "test-cp",
                "task_id": "test-task",
            },
        )
        assert response.status_code == 200
    assert verifier.on_event_loop is False


def test_audit_oversize_request_is_rejected_before_parsing(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        response = client.post(
            "/api/v1/audit/verify",
            content=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(70 * 1024 * 1024),
            },
        )
        assert response.status_code == 413


def _app(tmp_path):
    (tmp_path / "workspace/reports").mkdir(parents=True, exist_ok=True)
    return create_app(
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            runtime_mode=RuntimeMode.OFFLINE,
            workspace_root=tmp_path / "workspace",
            core_event_log_path=tmp_path / "events.jsonl",
            core_memory_path=tmp_path / "memory.json",
            core_outbox_path=tmp_path / "outbox.jsonl",
        )
    )


def _body(
    client, *, tool="write_file", action="write_file", effect="WRITE", skill="writer"
):
    created = client.post(
        "/api/v1/contracts",
        json={
            "session_id": "session-http",
            "task_id": "task-http",
            "user_id": "user-http",
            "goals": ["Write only within trusted permissions"],
            "allowed": [
                {
                    "tool": "*",
                    "action": action,
                    "resource": "reports/a.txt",
                    "effect": effect,
                }
            ],
            "policy_version": "test:1",
            "tool_manifest_digest": "test-manifest",
        },
    ).json()["data"]
    confirmed = client.post(
        f"/api/v1/contracts/{created['contract']['contract_id']}/confirm",
        json={"version": 1, "confirmed_by": "user-http"},
    ).json()["data"]
    grant = {
        "subject": "*",
        "skill": "*",
        "tool": "*",
        "action": "*",
        "resource": "*",
        "effect": "ALLOW",
        "scope": "GLOBAL",
        "source": "client",
    }
    return {
        "envelope": {
            "request_id": "request-http",
            "session_id": "session-http",
            "task_id": "task-http",
            "contract_ref": confirmed["ref"],
            "skill_ref": skill,
            "tool": tool,
            "action": action,
            "effect_class": effect,
            "resource": "reports/a.txt",
            "canonical_args": {"path": "reports/a.txt", "content": "hello"},
        },
        "permissions": {
            name: [grant] for name in ("user_grants", "skill_grants", "system_grants")
        },
    }


def test_http_caller_grants_cannot_override_trusted_profile(tmp_path):
    app = _app(tmp_path)
    profile = deepcopy(DEFAULT_PROFILE)
    profile["allowed_actions"] = ["read_file"]
    asyncio.run(app.state.security_profile_store.create_version("default", profile))
    with TestClient(app) as client:
        response = client.post("/api/v1/tool-calls/evaluate", json=_body(client))
        assert response.status_code == 200
        assert response.json()["data"]["decision"]["decision"] == "DENY"
        rejected = client.post("/api/v1/tool-calls/request-http/execute")
        assert rejected.status_code == 409
    assert not (tmp_path / "workspace/reports/a.txt").exists()


def test_http_cannot_disguise_write_tool_as_read(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        response = client.post(
            "/api/v1/tool-calls/evaluate",
            json=_body(client, action="read_file", effect="READ"),
        )
        assert response.status_code == 409
        assert client.post("/api/v1/tool-calls/request-http/execute").status_code == 409
    assert not (tmp_path / "workspace/reports/a.txt").exists()


def test_unregistered_skill_cannot_supply_its_own_grants(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        response = client.post(
            "/api/v1/tool-calls/evaluate",
            json=_body(client, skill="invented-admin-skill"),
        )
        assert response.status_code == 200
        assert response.json()["data"]["decision"]["reason_code"] == "SKILL_LIMIT"


def test_server_authority_is_rechecked_between_evaluate_and_execute(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/v1/tool-calls/evaluate", json=_body(client))
        assert response.json()["data"]["decision"]["decision"] == "ALLOW"
        profile = deepcopy(DEFAULT_PROFILE)
        profile["allowed_actions"] = ["read_file"]
        asyncio.run(app.state.security_profile_store.create_version("default", profile))
        rejected = client.post("/api/v1/tool-calls/request-http/execute")
        assert rejected.status_code == 409
        assert "USER_DENY" in str(rejected.json())
    assert not (tmp_path / "workspace/reports/a.txt").exists()


def test_workspace_alias_cannot_escape_authorized_resource_scope(tmp_path):
    app = _app(tmp_path)
    target = tmp_path / "workspace/private"
    target.mkdir()
    link = tmp_path / "workspace/reports/redirect"
    if os.name == "nt":
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "New-Item -ItemType Junction -Path $env:AEGIS_TEST_LINK "
                "-Target $env:AEGIS_TEST_TARGET | Out-Null",
            ],
            env={
                **os.environ,
                "AEGIS_TEST_LINK": str(link),
                "AEGIS_TEST_TARGET": str(target),
            },
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)
    with TestClient(app) as client:
        body = _body(client)
        body["envelope"]["resource"] = "reports/redirect/result.txt"
        body["envelope"]["canonical_args"]["path"] = "reports/redirect/result.txt"
        response = client.post("/api/v1/tool-calls/evaluate", json=body)
        assert response.status_code == 409
        assert "RESOURCE_MISMATCH" in str(response.json())
    assert not (target / "result.txt").exists()


def test_chunked_body_limit_counts_actual_bytes_before_app():
    from ra_agent.api.body_limits import CoreBodyLimitMiddleware

    async def exercise():
        called = False
        replies = []
        chunks = iter(
            [
                {"type": "http.request", "body": b"x" * 40000, "more_body": True},
                {"type": "http.request", "body": b"x" * 40000, "more_body": False},
            ]
        )

        async def app(scope, receive, send):
            nonlocal called
            called = True

        async def receive():
            return next(chunks)

        async def send(message):
            replies.append(message)

        await CoreBodyLimitMiddleware(app, max_write_bytes=1024)(
            {"type": "http", "path": "/api/v1/tool-calls/evaluate", "headers": []},
            receive,
            send,
        )
        assert not called
        assert replies[0]["status"] == 413

    asyncio.run(exercise())
