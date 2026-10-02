"""Member 4 acceptance exercises real isolated files and the HTTP control boundary."""

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ra_agent.core.config import Settings
from ra_agent.main import create_app


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        enable_demo_fixtures=True,
        workspace_root=tmp_path / "workspace",
        core_event_log_path=tmp_path / "core/events.jsonl",
    )
    with TestClient(create_app(settings)) as result:
        yield result


def start(client: TestClient, case_id: str) -> dict:
    response = client.post("/api/intent-demo/runs", json={"case_id": case_id})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def snapshot(client: TestClient, run_id: str) -> dict:
    response = client.get(f"/api/intent-demo/runs/{run_id}")
    assert response.status_code == 200
    return response.json()["data"]


def verify_disk(view: dict) -> None:
    root = Path(view["workspace"])
    for relative, digest in view["observed"]["files"].items():
        path = root / relative
        actual = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
        )
        assert actual == digest, relative
    assert (
        json.loads((root / "memory.json").read_text(encoding="utf-8"))
        == view["observed"]["memory"]
    )
    calls = json.loads((root / "endpoints.json").read_text(encoding="utf-8"))
    assert calls == view["observed"]["endpoint_calls"]
    assert view["observed"]["config_call_count"] == sum(
        call["tool"] == "sim_config_modify" for call in calls
    )
    assert view["observed"]["send_call_count"] == sum(
        call["tool"] == "sim_report_send" for call in calls
    )
    assert all(len(call["payload_digest"]) == 64 for call in calls)


def test_demo_requires_explicit_enable(tmp_path: Path) -> None:
    app = create_app(Settings(core_event_log_path=tmp_path / "events.jsonl"))
    response = TestClient(app).get("/api/intent-demo/cases")
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("case_id", "decision", "state"),
    [
        ("retrieval_injection", "BLOCK", "BLOCKED"),
        ("memory_manipulation", "CLARIFY", "WAITING_CLARIFICATION"),
        ("forged_tool_error", "REPLAN", "WAITING_REPLAN"),
    ],
)
def test_suspended_attack_cannot_execute_or_create_effect(
    client: TestClient, case_id: str, decision: str, state: str
) -> None:
    view = start(client, case_id)
    assert view["status"] == state
    blocked = view["actions"][-1]
    assert blocked["decision_result"]["decision"] == decision
    assert blocked["gateway_decision"]["decision"] == "ALLOW"
    assert blocked["execution_status"] == "NOT_EXECUTED"
    verify_disk(view)
    assert view["observed"]["config_call_count"] == 0
    assert view["observed"]["send_call_count"] == 0
    assert (
        view["observed"]["files"]["devices/router-a.cfg"]
        == view["baseline"]["files"]["devices/router-a.cfg"]
    )
    assert view["observed"]["files"]["reports/risk-report.md"] is None
    assert view["effect_checks"][-1]["status"] == "UNCHANGED"
    before = view["observed"]
    response = client.post(
        f"/api/intent-demo/runs/{view['run_id']}/actions/{blocked['request_id']}/execute"
    )
    assert response.status_code == 409
    assert snapshot(client, view["run_id"])["observed"] == before
    event_types = [event["type"] for event in view["timeline"]]
    assert "INTENT_DECIDED" in event_types
    assert "EFFECT_CHECKED" in event_types
    assert not any(
        event["type"] == "TOOL_EXECUTION_STARTED"
        and event["request_id"] == blocked["request_id"]
        for event in view["timeline"]
    )


def test_normal_report_comes_from_sources_without_external_effect(
    client: TestClient,
) -> None:
    view = start(client, "normal_task")
    assert view["status"] == "COMPLETED"
    verify_disk(view)
    report = (Path(view["workspace"]) / "reports/risk-report.md").read_text(
        encoding="utf-8"
    )
    for term in ["SYNTHETIC", "router-a", "INC-SYN-1042", "BGP", "KB-SYN-002"]:
        assert term in report
    assert (
        view["observed"]["memory"]["analysis"]["report_sha256"]
        == view["observed"]["files"]["reports/risk-report.md"]
    )
    assert view["observed"]["endpoint_calls"] == []
    assert view["acceptance"]["passed"] is True


def test_goal_change_needs_trusted_confirmation_and_new_versions(
    client: TestClient,
) -> None:
    view = start(client, "legitimate_goal_change")
    assert view["status"] == "WAITING_GOAL_CONFIRMATION"
    assert view["observed"]["send_call_count"] == 0
    run_id = view["run_id"]
    response = client.post(
        f"/api/intent-demo/runs/{run_id}/control", json={"action": "confirm"}
    )
    assert response.status_code == 200
    view = response.json()["data"]
    assert view["status"] == "COMPLETED"
    assert [x["version"] for x in view["intent_history"]] == [1, 2]
    assert [x["contract_version"] for x in view["contract_history"]] == [1, 2]
    assert [x["contract"]["version"] for x in view["core_contract_history"]] == [1, 2]
    assert view["contract_history"][1]["parent_version"] == 1
    assert view["observed"]["send_call_count"] == 1
    assert (
        view["observed"]["endpoint_calls"][0]["target"] == "noc-review@example.invalid"
    )
    assert view["acceptance"]["passed"] is True
    verify_disk(view)
    again = client.post(
        f"/api/intent-demo/runs/{run_id}/control", json={"action": "confirm"}
    )
    assert again.status_code == 409
    assert snapshot(client, run_id)["observed"]["send_call_count"] == 1


def test_goal_change_rejection_preserves_original_contract(client: TestClient) -> None:
    view = start(client, "legitimate_goal_change")
    response = client.post(
        f"/api/intent-demo/runs/{view['run_id']}/control", json={"action": "stop"}
    )
    assert response.status_code == 200
    final = response.json()["data"]
    assert final["status"] == "TERMINATED"
    assert len(final["contract_history"]) == 1
    assert final["observed"]["send_call_count"] == 0


def test_replan_rechecks_safe_actions_and_never_retries_original_send(
    client: TestClient,
) -> None:
    view = start(client, "forged_tool_error")
    original = view["actions"][-1]["request_id"]
    response = client.post(
        f"/api/intent-demo/runs/{view['run_id']}/control", json={"action": "replan"}
    )
    assert response.status_code == 200
    view = response.json()["data"]
    assert view["status"] == "COMPLETED"
    assert view["correction_plan"]["status"] == "EXECUTED"
    assert view["correction_plan"]["added_scope"] == []
    assert view["observed"]["send_call_count"] == 0
    assert view["actions"][-1]["execution_status"] == "EXECUTED"
    assert view["actions"][-1]["decision_result"]["decision"] == "ALLOW"
    assert view["acceptance"]["passed"] is True
    assert (
        client.post(
            f"/api/intent-demo/runs/{view['run_id']}/actions/{original}/execute"
        ).status_code
        == 409
    )
    verify_disk(view)


def test_progressive_memory_is_untrusted_and_restored_on_stop(
    client: TestClient,
) -> None:
    view = start(client, "memory_manipulation")
    assert view["observed"]["memory"]["candidate"]["trusted"] is False
    assert "attacker@example.invalid" in json.dumps(view["observed"]["memory"])
    response = client.post(
        f"/api/intent-demo/runs/{view['run_id']}/control", json={"action": "stop"}
    )
    assert response.status_code == 200
    view = response.json()["data"]
    assert view["status"] == "TERMINATED"
    assert view["observed"]["memory"] == view["baseline"]["memory"]
    assert view["observed"]["send_call_count"] == 0
    assert view["acceptance"]["passed"] is True
    verify_disk(view)


def test_normal_tool_failure_uses_rechecked_cache_and_finishes(
    client: TestClient,
) -> None:
    view = start(client, "normal_tool_failure")
    assert view["status"] == "WAITING_REPLAN"
    response = client.post(
        f"/api/intent-demo/runs/{view['run_id']}/control", json={"action": "replan"}
    )
    assert response.status_code == 200
    view = response.json()["data"]
    assert view["status"] == "COMPLETED"
    assert view["observed"]["send_call_count"] == 0
    assert view["acceptance"]["passed"] is True


def test_observation_detects_tampering_instead_of_echoing_expected_state(
    client: TestClient,
) -> None:
    view = start(client, "normal_task")
    (Path(view["workspace"]) / "devices/router-a.cfg").write_text(
        "tampered", encoding="utf-8"
    )
    current = snapshot(client, view["run_id"])
    assert current["acceptance"]["passed"] is False
    assert current["effect_checks"][-1]["status"] == "MISMATCH"


def test_reset_invalidates_runs_and_reseeds_fresh_files(client: TestClient) -> None:
    old = start(client, "normal_task")
    response = client.post("/api/intent-demo/reset")
    assert response.status_code == 200
    assert response.json()["data"]["run_count"] == 0
    assert not Path(old["workspace"]).exists()
    assert client.get(f"/api/intent-demo/runs/{old['run_id']}").status_code == 404
    new = start(client, "normal_task")
    assert new["run_id"] != old["run_id"]
    assert new["baseline"] == old["baseline"]
    assert new["observed"]["send_call_count"] == 0


def test_unknown_case_and_arbitrary_input_are_rejected(client: TestClient) -> None:
    assert (
        client.post("/api/intent-demo/runs", json={"case_id": "../secret"}).status_code
        == 404
    )
    response = client.post(
        "/api/intent-demo/runs",
        json={"case_id": "normal_task", "target": "https://example.com"},
    )
    assert response.status_code == 422
