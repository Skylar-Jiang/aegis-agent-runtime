from fastapi.testclient import TestClient

from ra_agent.core.config import RuntimeMode, Settings
from ra_agent.main import create_app


def _grant(source: str, *, subject: str = "*", skill: str = "*") -> dict[str, object]:
    return {
        "subject": subject,
        "skill": skill,
        "tool": "list_dir",
        "action": "list_dir",
        "resource": "**",
        "effect": "ALLOW",
        "scope": "GLOBAL",
        "expires_at": None,
        "source": source,
        "scope_ref": None,
    }


def test_core_v1_session_contract_permission_and_gateway_flow(tmp_path) -> None:
    app = create_app(Settings(
        runtime_mode=RuntimeMode.OFFLINE,
        workspace_root=tmp_path / "workspace",
        core_event_log_path=tmp_path / "events.jsonl",
        core_memory_path=tmp_path / "memory.json",
        core_outbox_path=tmp_path / "outbox.jsonl",
    ))
    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["data"]["signature_provider"] == "FakeSignatureProvider"

        session = client.post(
            "/api/v1/sessions", json={"user_id": "user-1", "title": "PR1 test"}
        ).json()["data"]
        draft = client.post(
            f"/api/v1/sessions/{session['session_id']}/tasks",
            json={"objective": "List the workspace"},
        ).json()["data"]
        contract_id = draft["contract"]["contract"]["contract_id"]

        confirmed_response = client.post(
            f"/api/v1/contracts/{contract_id}/confirm",
            json={"version": 1, "confirmed_by": "user-1"},
        )
        assert confirmed_response.status_code == 200
        confirmed = confirmed_response.json()["data"]

        evaluation = client.post(
            "/api/v1/tool-calls/evaluate",
            json={
                "envelope": {
                    "request_id": "request-api-1",
                    "task_id": draft["task_id"],
                    "session_id": session["session_id"],
                    "contract_ref": confirmed["ref"],
                    "skill_ref": "workspace-reader",
                    "tool": "list_dir",
                    "action": "list_dir",
                    "canonical_args": {"path": "."},
                    "resource": ".",
                    "effect_class": "READ",
                },
                "permissions": {
                    "user_grants": [_grant("user", subject="user-1")],
                    "skill_grants": [_grant("skill", skill="workspace-reader")],
                    "system_grants": [_grant("system")],
                },
            },
        )
        assert evaluation.status_code == 200
        assert evaluation.json()["data"]["decision"]["decision"] == "ALLOW"

        effective = client.get(
            "/api/v1/permissions/effective", params={"request_id": "request-api-1"}
        )
        assert effective.status_code == 200
        assert set(effective.json()["data"]["matched_sources"]) == {"user", "skill", "system"}

        executed = client.post("/api/v1/tool-calls/request-api-1/execute")
        assert executed.status_code == 200
        result = executed.json()["data"]["result"]
        assert result["tool"] == "list_dir"
        assert result["path"] == "."
        events = client.get(f"/api/v1/tasks/{draft['task_id']}/events")
        assert events.status_code == 200
        event_types = [item["type"] for item in events.json()["data"]]
        assert "EXECUTION_FINISHED" in event_types


def test_contract_confirmation_requires_the_reviewed_version() -> None:
    app = create_app(Settings(runtime_mode=RuntimeMode.OFFLINE))
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/contracts",
            json={
                "session_id": "session-race",
                "task_id": "task-race",
                "user_id": "user-race",
                "goals": ["reviewed version"],
                "allowed": [{"action": "list_dir", "resource": "**"}],
                "policy_version": "policy:1",
                "tool_manifest_digest": "tools:1",
            },
        ).json()["data"]
        contract_id = first["contract"]["contract_id"]
        second = client.post(
            f"/api/v1/contracts/{contract_id}/versions",
            json={"goals": ["unreviewed newer version"]},
        ).json()["data"]

        stale_confirmation = client.post(
            f"/api/v1/contracts/{contract_id}/confirm",
            json={"version": 1, "confirmed_by": "user-race"},
        )
        assert stale_confirmation.status_code == 409

        current_confirmation = client.post(
            f"/api/v1/contracts/{contract_id}/confirm",
            json={"version": second["contract"]["version"], "confirmed_by": "user-race"},
        )
        assert current_confirmation.status_code == 200
        assert current_confirmation.json()["data"]["contract"]["goals"] == [
            "unreviewed newer version"
        ]


def test_core_v1_real_sm2_stack_persists_and_verifies_after_restart(tmp_path) -> None:
    import asyncio
    import json

    from ra_agent.audit import AuditVerifier, FileCheckpointStore
    from ra_agent.core.config import CoreCryptoMode
    from ra_agent.crypto import (
        EnvelopeService,
        OpenSSLSignatureProvider,
        generate_sm2_key,
    )

    private_key = tmp_path / "core-private.pem"
    public = generate_sm2_key(private_key)
    public_keys = tmp_path / "public-keys.json"
    public_keys.write_text(json.dumps({"core-app-key": public}), encoding="utf-8")
    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        runtime_mode=RuntimeMode.OFFLINE,
        core_crypto_mode=CoreCryptoMode.SM2,
        core_sm2_key_id="core-app-key",
        core_sm2_public_keys_path=public_keys,
        core_sm2_private_key_path=private_key,
        core_event_log_path=tmp_path / "events.jsonl",
        core_evidence_root=tmp_path / "evidence",
        core_audit_checkpoint_root=tmp_path / "anchors",
    )

    app = create_app(settings)
    with TestClient(app) as client:
        health = client.get("/api/v1/health").json()["data"]
        assert health["signature_provider"] == "OpenSSLSignatureProvider"
        assert health["event_store"] == "SqliteEventStore"
        assert health["crypto_mode"] == "sm2"
        assert health["audit_exporter"] == "AuditExportService"

        created = client.post(
            "/api/v1/contracts",
            json={
                "session_id": "session-real",
                "task_id": "task-real",
                "user_id": "user-real",
                "goals": ["list the workspace"],
                "allowed": [
                    {
                        "tool": "list_dir",
                        "action": "list_dir",
                        "resource": "**",
                        "effect": "READ",
                    }
                ],
                "policy_version": "policy:real",
                "tool_manifest_digest": "b" * 64,
            },
        ).json()["data"]
        confirmed = client.post(
            f"/api/v1/contracts/{created['contract']['contract_id']}/confirm",
            json={"version": 1, "confirmed_by": "user-real"},
        ).json()["data"]
        evaluated = client.post(
            "/api/v1/tool-calls/evaluate",
            json={
                "envelope": {
                    "request_id": "request-real",
                    "task_id": "task-real",
                    "session_id": "session-real",
                    "contract_ref": confirmed["ref"],
                    "skill_ref": "workspace-reader",
                    "tool": "list_dir",
                    "action": "list_dir",
                    "canonical_args": {"path": "."},
                    "resource": ".",
                    "effect_class": "READ",
                },
                "permissions": {
                    "user_grants": [_grant("user", subject="user-real")],
                    "skill_grants": [_grant("skill", skill="workspace-reader")],
                    "system_grants": [_grant("system")],
                },
            },
        )
        assert evaluated.status_code == 200
        assert client.post("/api/v1/tool-calls/request-real/execute").status_code == 200

        exported = client.post(
            "/api/v1/audit/export",
            json={"task_id": "task-real", "checkpoint_id": "checkpoint-http"},
        )
        assert exported.status_code == 200
        verified = client.post(
            "/api/v1/audit/verify",
            json={
                "bundle": exported.json()["data"],
                "trusted_checkpoint_id": "checkpoint-http",
                "task_id": "task-real",
            },
        )
        assert verified.status_code == 200
        assert verified.json()["data"]["valid"] is True

    restarted = create_app(settings)
    bundle = asyncio.run(
        restarted.state.core_audit_exporter.export_task(
            task_id="task-real",
            checkpoint_id="checkpoint-after-restart",
        )
    )
    readonly = EnvelopeService(OpenSSLSignatureProvider({"core-app-key": public}))
    verifier = AuditVerifier(
        readonly,
        FileCheckpointStore(tmp_path / "anchors", readonly),
    )
    result = verifier.verify_bundle(
        bundle,
        trusted_checkpoint_id="checkpoint-after-restart",
        task_id="task-real",
    )
    assert result.valid
    assert result.verified_events == 8


def test_core_v1_sm2_mode_rejects_missing_trusted_key_configuration(tmp_path) -> None:
    import pytest

    from ra_agent.core.config import CoreCryptoMode
    from ra_agent.crypto import CryptoError

    settings = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        runtime_mode=RuntimeMode.OFFLINE,
        core_crypto_mode=CoreCryptoMode.SM2,
        core_sm2_key_id="missing-key",
        core_sm2_public_keys_path=tmp_path / "missing-public-keys.json",
        core_sm2_private_key_path=tmp_path / "missing-private-key.pem",
    )
    with pytest.raises(CryptoError) as unavailable:
        create_app(settings)
    assert unavailable.value.code == "KEY_UNAVAILABLE"
