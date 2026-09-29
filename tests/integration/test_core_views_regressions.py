"""Core read-view regressions for durable restore and recorded provenance."""

import hashlib
import json
import os
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from ra_agent.api import core_views
from tests.integration.test_core_http_hardening import _app, _body


def test_recorded_experiment_time_prefers_result_completion_and_metadata(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(core_views, "_EVIDENCE_ROOT", tmp_path)
    monkeypatch.setattr(
        core_views,
        "_EXPERIMENTS",
        {"benchmark": "benchmark", "http-controls": "controls", "fallback": "fallback"},
    )
    fixtures = {
        "benchmark": {
            "started_at": "2026-09-23T04:41:39+00:00",
            "finished_at": "2026-09-23T04:45:42+00:00",
        },
        "http-controls": {"created_at": "2026-09-23T05:03:22+00:00"},
        "fallback": {"generated_at": "not-a-time", "started_at": 123},
    }
    for name, payload in fixtures.items():
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        os.utime(path, (946684800, 946684800))

    result = core_views._recorded_experiments()
    runs = {run["id"]: run for run in result["runs"]}
    assert runs["benchmark"]["recorded_at"] == "2026-09-23T04:45:42+00:00"
    assert runs["http-controls"]["recorded_at"] == "2026-09-23T05:03:22+00:00"
    assert (
        runs["fallback"]["recorded_at"]
        == datetime.fromtimestamp(946684800, UTC).isoformat()
    )
    raw = (tmp_path / "benchmark.json").read_bytes()
    assert runs["benchmark"]["sha256"] == hashlib.sha256(raw).hexdigest()


def test_recorded_experiment_unavailable_entries_do_not_hide_valid_runs(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(core_views, "_EVIDENCE_ROOT", tmp_path)
    monkeypatch.setattr(
        core_views,
        "_EXPERIMENTS",
        {
            "valid": "valid",
            "missing": "missing",
            "invalid": "invalid",
            "oversize": "oversize",
        },
    )
    (tmp_path / "valid.json").write_text('{"generated_at":"2026-09-23T04:55:46+00:00"}')
    (tmp_path / "invalid.json").write_text("{")
    (tmp_path / "oversize.json").write_bytes(b" " * (core_views._MAX_RESULT_BYTES + 1))

    result = core_views._recorded_experiments()
    assert [run["id"] for run in result["runs"]] == ["valid"]
    assert {item["id"] for item in result["unavailable"]} == {
        "missing",
        "invalid",
        "oversize",
    }


def test_snapshot_tracks_latest_contract_per_task_across_sessions_and_restart(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        body = _body(client)
        client.post("/api/v1/tool-calls/evaluate", json=body).raise_for_status()
        session = client.post(
            "/api/v1/sessions",
            json={
                "user_id": "different-user",
                "title": "Other",
                "security_profile_id": "default",
            },
        ).json()["data"]
        other = client.post(
            f"/api/v1/sessions/{session['session_id']}/tasks",
            json={"objective": "Other task"},
        ).json()["data"]
        cid = body["envelope"]["contract_ref"]["contract_id"]
        updated = client.post(
            f"/api/v1/contracts/{cid}/versions", json={"goals": ["Revised objective"]}
        ).json()["data"]
        first = client.get("/api/v1/tasks/task-http/snapshot").json()["data"]
        second = client.get(f"/api/v1/tasks/{other['task_id']}/snapshot").json()["data"]
        assert first["task"]["contract"]["ref"] == updated["ref"]
        assert first["task"]["status"] == "DRAFT"
        assert first["latest_request"] is None
        assert second["task"]["session_id"] == session["session_id"]
        assert second["task"]["contract"]["ref"]["version"] == 1
        assert second["latest_request"] is None

    with TestClient(_app(tmp_path)) as restarted:
        assert restarted.get("/api/v1/tasks/task-http/snapshot").json()["data"] == first
        assert (
            restarted.get(f"/api/v1/tasks/{other['task_id']}/snapshot").json()["data"]
            == second
        )
