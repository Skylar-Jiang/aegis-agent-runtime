"""Real HTTP recovery, preserving scopes, bounded retries and safe failure."""

import pytest
from experiments.intent.dataset import load
from fastapi.testclient import TestClient
from ra_agent.core.config import Settings
from ra_agent.main import create_app
from scripts.start_core import prepare
from scripts.verify_intent_local import envelope, new_contract, post


@pytest.fixture
def recovery(tmp_path, monkeypatch):
    from scripts.verify_intent_local import ROOT

    for key, value in prepare(ROOT, runtime=tmp_path).items():
        monkeypatch.setenv(key, value)
    app = create_app(Settings())
    case = next(c for c in load() if c["case_id"] == "retrieval-0-0")
    source = tmp_path / "workspace" / f"workspace/{case['case_id']}/inputs/log.txt"
    source.parent.mkdir(parents=True)
    source.write_text(case["fixtures"]["inputs/log.txt"], encoding="utf-8")
    with TestClient(app) as client:
        record = new_contract(client, case, "reports/recovery.txt")
        read = envelope(
            record,
            "read_file",
            f"workspace/{case['case_id']}/inputs/log.txt",
            {"path": f"workspace/{case['case_id']}/inputs/log.txt"},
        )
        post(
            client, "/api/v1/tool-calls/evaluate", {"envelope": read, "permissions": {}}
        )
        post(client, f"/api/v1/tool-calls/{read['request_id']}/execute")
        attack = envelope(
            record,
            "write_file",
            "reports/recovery.txt",
            {"path": "reports/recovery.txt", "content": "advertisement"},
        )
        evaluated = post(
            client,
            "/api/v1/tool-calls/evaluate",
            {"envelope": attack, "permissions": {}},
        )
        assert evaluated["intent"]["disposition"] == "SAFE_STOP"
        yield client, record, source, tmp_path / "workspace/reports/recovery.txt", case


def propose(client, record):
    return post(
        client, f"/api/v1/tasks/{record['contract']['task_id']}/intent/recovery"
    )


def test_recovery_executes_real_actions_only_after_confirmation(recovery):
    client, record, source, target, case = recovery
    plan = propose(client, record)
    assert plan["added_scope"] == [] and not target.exists()
    done = post(
        client,
        f"/api/v1/intent/recovery/{plan['plan_id']}/approve",
        {"confirmed_by": "test-operator"},
    )
    assert done["status"] == "COMPLETED", done
    assert target.read_text(encoding="utf-8") == case["gold"]["required_report"]
    assert len(done["executions"]) == 2
    assert (
        client.post(
            f"/api/v1/intent/recovery/{plan['plan_id']}/approve",
            json={"confirmed_by": "test-operator"},
        ).status_code
        == 409
    )


def test_changed_source_safely_stops_without_report_effect(recovery):
    client, record, source, target, _ = recovery
    plan = propose(client, record)
    source.write_text("changed evidence", encoding="utf-8")
    done = post(
        client,
        f"/api/v1/intent/recovery/{plan['plan_id']}/approve",
        {"confirmed_by": "test-operator"},
    )
    assert done["status"] == "SAFE_STOPPED"
    assert not target.exists()
    request = envelope(record, "read_file", str(source), {"path": str(source)})
    assert (
        client.post(
            "/api/v1/tool-calls/evaluate", json={"envelope": request, "permissions": {}}
        ).status_code
        == 409
    )


def test_stale_recovery_does_not_override_user_revision(recovery):
    client, record, _, target, _ = recovery
    plan = propose(client, record)
    draft = post(
        client,
        f"/api/v1/contracts/{record['ref']['contract_id']}/versions",
        {"goals": ["new trusted user goal"]},
    )
    post(
        client,
        f"/api/v1/contracts/{record['ref']['contract_id']}/confirm",
        {"version": draft["ref"]["version"], "confirmed_by": "test-operator"},
    )
    assert (
        client.post(
            f"/api/v1/intent/recovery/{plan['plan_id']}/approve",
            json={"confirmed_by": "test-operator"},
        ).status_code
        == 409
    )
    assert not target.exists()


def test_proposal_budget_is_bounded(recovery):
    client, record, _, target, _ = recovery
    propose(client, record)
    propose(client, record)
    assert (
        client.post(
            f"/api/v1/tasks/{record['contract']['task_id']}/intent/recovery"
        ).status_code
        == 409
    )
    assert not target.exists()


def test_live_policy_file_rechecks_new_requests(recovery):
    import json

    client, _, _, target, case = recovery
    runtime = target.parents[2]
    (runtime / "intent-policy.json").write_text(
        json.dumps(
            {
                "schema_version": "intent-policy-release-v1",
                "version": "simulated-reviewed-policy:2",
                "repeat_limit": 2,
                "approved_by": "simulated-test-operator",
                "reviewers": ["simulated-a", "simulated-b"],
                "validation_ref": "simulated-unit-fixture",
            }
        ),
        encoding="utf-8",
    )
    record = new_contract(client, case, "reports/live-policy.txt")
    source = f"workspace/{case['case_id']}/inputs/log.txt"
    first = envelope(record, "read_file", source, {"path": source})
    checked = post(
        client, "/api/v1/tool-calls/evaluate", {"envelope": first, "permissions": {}}
    )
    assert checked["intent"]["policy_version"] == "simulated-reviewed-policy:2"
    post(client, f"/api/v1/tool-calls/{first['request_id']}/execute")
    second = envelope(record, "read_file", source, {"path": source})
    checked = post(
        client, "/api/v1/tool-calls/evaluate", {"envelope": second, "permissions": {}}
    )
    assert checked["intent"]["disposition"] == "SAFE_STOP"


def test_malformed_live_policy_fails_closed_before_effect(recovery):
    client, _, _, target, case = recovery
    (target.parents[2] / "intent-policy.json").write_text("{broken", encoding="utf-8")
    record = new_contract(client, case, "reports/malformed-policy.txt")
    source = f"workspace/{case['case_id']}/inputs/log.txt"
    request = envelope(record, "read_file", source, {"path": source})
    checked = post(
        client, "/api/v1/tool-calls/evaluate", {"envelope": request, "permissions": {}}
    )
    assert checked["intent"]["disposition"] == "SAFE_STOP"
    assert "intent_check_unavailable" in checked["intent"]["trigger_dimensions"]
    assert (
        client.post(f"/api/v1/tool-calls/{request['request_id']}/execute").status_code
        == 409
    )
