"""Public HTTP regressions for contract, provenance and event-loop isolation."""

import asyncio
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from ra_agent.database.workbench_store import DEFAULT_PROFILE
from ra_agent.gateway.state import CoreStateStore
from tests.integration.test_core_http_hardening import _app, _body


def test_binary_read_returns_structured_rejection(tmp_path):
    app = _app(tmp_path)
    target = tmp_path / "workspace/reports/a.txt"
    target.write_bytes(b"\xff\xfe\x00")
    with TestClient(app, raise_server_exceptions=False) as client:
        body = _body(client, tool="read_file", action="read_file", effect="READ", skill="core-ui")
        evaluated = client.post("/api/v1/tool-calls/evaluate", json=body)
        assert evaluated.json()["data"]["decision"]["decision"] == "ALLOW"
        response = client.post("/api/v1/tool-calls/request-http/execute")
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == "TOOL_EXECUTION_REJECTED"
        assert "UTF-8" in response.json()["detail"]["message"]
        # All admitted adapter failures keep the existing conservative policy.
        retry = client.post("/api/v1/tool-calls/request-http/execute")
        assert retry.status_code == 409
        assert "UNKNOWN" in retry.text
    assert target.read_bytes() == b"\xff\xfe\x00"


@pytest.mark.parametrize("upgrade", [False, True])
def test_contract_tightening_counts_prior_unlimited_writes(tmp_path, upgrade):
    app = _app(tmp_path)
    with TestClient(app) as client:
        body = _body(client)
        client.post("/api/v1/tool-calls/evaluate", json=body).raise_for_status()
        client.post("/api/v1/tool-calls/request-http/execute").raise_for_status()
        if upgrade:
            with app.state.core_state_store.transaction() as tx:
                tx._connection.execute(
                    "DELETE FROM core_state WHERE namespace='quotas'"
                )
        cid = body["envelope"]["contract_ref"]["contract_id"]
        client.post(
            f"/api/v1/contracts/{cid}/versions", json={"limits": {"max_bytes": 5}}
        ).raise_for_status()
        body["envelope"]["contract_ref"] = client.post(
            f"/api/v1/contracts/{cid}/confirm",
            json={"version": 2, "confirmed_by": "user-http"},
        ).json()["data"]["ref"]
        body["envelope"]["request_id"] = "second"
        body["envelope"]["canonical_args"]["content"] = "!"
        client.post("/api/v1/tool-calls/evaluate", json=body).raise_for_status()
        result = client.post("/api/v1/tool-calls/second/execute")
        assert result.status_code == 409, result.text
        assert "LIMIT_EXCEEDED: max_bytes" in result.text
        assert (tmp_path / "workspace/reports/a.txt").read_text() == "hello"


def test_reconfirm_preserves_pending_confirmation_and_completed_status(tmp_path):
    app = _app(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        body = _body(client)
        cid = body["envelope"]["contract_ref"]["contract_id"]
        client.post(
            f"/api/v1/contracts/{cid}/versions",
            json={"confirmation": {"required_actions": ["write_file"]}},
        ).raise_for_status()
        url = f"/api/v1/contracts/{cid}/confirm"
        confirmation = {"version": 2, "confirmed_by": "user-http"}
        first = client.post(url, json=confirmation).json()["data"]
        body["envelope"]["contract_ref"] = first["ref"]
        decision = client.post("/api/v1/tool-calls/evaluate", json=body).json()["data"]
        again = client.post(url, json=confirmation)
        assert again.json()["data"] == first
        evaluated = client.post("/api/v1/tool-calls/evaluate", json=body)
        assert evaluated.status_code == 200, evaluated.text
        assert (
            evaluated.json()["data"]["decision"]["confirmation_id"]
            == decision["decision"]["confirmation_id"]
        )
        aid = decision["decision"]["confirmation_id"]
        client.post(
            f"/api/v1/confirmations/{aid}/resolve",
            json={"confirmed": True, "resolved_by": "user-http"},
        ).raise_for_status()
        client.post("/api/v1/tool-calls/request-http/execute").raise_for_status()
        client.post(url, json=confirmation).raise_for_status()
        with app.state.core_state_store.transaction() as tx:
            assert tx.get("task_lifecycle", "task-http")["status"] == "EXECUTED"
        events = client.get("/api/v1/tasks/task-http/events").json()["data"]
        # Once per version, despite retries before and after execution.
        assert sum(e["type"] == "CONTRACT_CONFIRMED" for e in events) == 2


def test_decision_records_actual_policy_snapshot_version(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        body = _body(client)
        profile = deepcopy(DEFAULT_PROFILE)
        profile["max_affected_objects"] = 5
        version = client.put("/api/security-profiles/default", json=profile).json()[
            "data"
        ]["version"]
        result = client.post("/api/v1/tool-calls/evaluate", json=body)
        result.raise_for_status()
        data = result.json()["data"]
        assert data["effective_permission"]["constraints"]["max_affected_objects"] == 5
        assert data["decision"]["versions"]["policy"] == f"default:{version}"
        assert data["decision"]["versions"]["contract_policy"] == "test:1"


def test_request_state_io_never_runs_on_http_event_loop(tmp_path, monkeypatch):
    app = _app(tmp_path)
    original = CoreStateStore.transaction
    calls_on_loop = []

    def checked(self, *args, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            calls_on_loop.append(True)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CoreStateStore, "transaction", checked)
    with TestClient(app) as client:
        body = _body(client)
        client.post("/api/v1/tool-calls/evaluate", json=body).raise_for_status()
        client.get(
            "/api/v1/permissions/effective", params={"request_id": "request-http"}
        ).raise_for_status()
        client.post("/api/v1/tool-calls/request-http/execute").raise_for_status()
    assert not calls_on_loop


def test_task_snapshot_restores_durable_execution_after_restart(tmp_path):
    with TestClient(_app(tmp_path)) as client:
        body = _body(client)
        client.post("/api/v1/tool-calls/evaluate", json=body).raise_for_status()
        client.post("/api/v1/tool-calls/request-http/execute").raise_for_status()
    with TestClient(_app(tmp_path)) as restarted:
        response = restarted.get("/api/v1/tasks/task-http/snapshot")
        assert response.status_code == 200, response.text
        snapshot = response.json()["data"]
        assert snapshot["task"]["status"] == "EXECUTED"
        assert snapshot["latest_request"]["envelope"] == body["envelope"]
        assert snapshot["latest_request"]["execution_state"] == "EXECUTED"
        assert "permissions" not in snapshot["latest_request"]
        assert restarted.get("/api/v1/tasks/missing/snapshot").status_code == 404


def test_core_experiments_returns_recorded_data_and_content_hash(tmp_path):
    import hashlib
    from pathlib import Path

    with TestClient(_app(tmp_path)) as client:
        response = client.get("/api/v1/experiments/core")
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["recorded"] is True
        benchmark = next(run for run in data["runs"] if run["id"] == "benchmark")
        raw = (Path(__file__).parents[2] / benchmark["source"]).read_bytes()
        assert benchmark["sha256"] == hashlib.sha256(raw).hexdigest()
        assert benchmark["data"]["environment"]["git_commit"]


@pytest.mark.asyncio
async def test_cancellation_after_result_commit_retains_cached_success(tmp_path):
    from ra_agent.contracts import ContractService
    from ra_agent.gateway import ToolGateway
    from ra_agent.permissions import PermissionResolver
    from tests.unit.core.test_gateway_hardening import (
        AppendExecutor,
        call,
        contract,
        permissions,
    )

    class CancelAfterCommit(CoreStateStore):
        async def run(self, operation, *, readonly=False):
            result = await super().run(operation, readonly=readonly)
            if operation.__name__ in {"persist_3", "save_execution"}:
                raise asyncio.CancelledError
            return result

    store = CancelAfterCommit(tmp_path / "state.sqlite3")
    contracts = ContractService(store=store)
    record = await contract(contracts)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        store=store,
        executor=AppendExecutor(tmp_path / "effects"),
    )
    await gateway.evaluate(call(record), permissions())
    with pytest.raises(asyncio.CancelledError):
        await gateway.execute("request-1")
    result = await gateway.execute("request-1")
    assert result.status == "EXECUTED"
    assert (tmp_path / "effects").read_text() == "approved\n"


@pytest.mark.asyncio
async def test_cancellation_after_quota_admission_cannot_reclaim_request(tmp_path):
    from ra_agent.contracts import ContractService
    from ra_agent.gateway import GatewayError, ToolGateway
    from ra_agent.permissions import PermissionResolver
    from tests.unit.core.test_gateway_hardening import (
        AppendExecutor,
        call,
        contract,
        permissions,
    )

    class CancelAfterAdmission(CoreStateStore):
        async def run(self, operation, *, readonly=False):
            result = await super().run(operation, readonly=readonly)
            if operation.__name__ in {"persist_2", "admit_execution"}:
                raise asyncio.CancelledError
            return result

    store = CancelAfterAdmission(tmp_path / "state.sqlite3")
    contracts = ContractService(store=store)
    record = await contract(contracts)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        store=store,
        executor=AppendExecutor(tmp_path / "effects"),
    )
    await gateway.evaluate(call(record), permissions())
    with pytest.raises(asyncio.CancelledError):
        await gateway.execute("request-1")
    state = await store.get("requests", "request-1")
    assert state["execution_state"] == "UNKNOWN"
    with pytest.raises(GatewayError, match="UNKNOWN"):
        await gateway.execute("request-1")
    assert not (tmp_path / "effects").exists()


def test_confirmation_retry_repairs_interrupted_audit_append(tmp_path, monkeypatch):
    app = _app(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        body = _body(client)
        cid = body["envelope"]["contract_ref"]["contract_id"]
        client.post(
            f"/api/v1/contracts/{cid}/versions", json={"goals": ["revised"]}
        ).raise_for_status()
        original = app.state.core_gateway.record_contract_event
        failed = False

        async def interrupted(*args, **kwargs):
            nonlocal failed
            if kwargs["event_type"] == "CONTRACT_CONFIRMED" and not failed:
                failed = True
                raise OSError("simulated audit storage failure")
            return await original(*args, **kwargs)

        monkeypatch.setattr(
            app.state.core_gateway, "record_contract_event", interrupted
        )
        url = f"/api/v1/contracts/{cid}/confirm"
        confirmation = {"version": 2, "confirmed_by": "user-http"}
        assert client.post(url, json=confirmation).status_code == 500
        client.post(url, json=confirmation).raise_for_status()
        client.post(url, json=confirmation).raise_for_status()
        events = client.get("/api/v1/tasks/task-http/events").json()["data"]
        assert sum(e["type"] == "CONTRACT_CONFIRMED" for e in events) == 2


@pytest.mark.asyncio
async def test_cancelled_claim_releases_only_its_own_pre_admission_state(tmp_path):
    from ra_agent.contracts import ContractService
    from ra_agent.gateway import ToolGateway
    from ra_agent.permissions import PermissionResolver
    from tests.unit.core.test_gateway_hardening import (
        AppendExecutor,
        call,
        contract,
        permissions,
    )

    class CancelClaimOnce(CoreStateStore):
        cancel = True

        async def run(self, operation, *, readonly=False):
            result = await super().run(operation, readonly=readonly)
            if operation.__name__ == "claim" and self.cancel:
                self.cancel = False
                raise asyncio.CancelledError
            return result

    store = CancelClaimOnce(tmp_path / "state.sqlite3")
    contracts = ContractService(store=store)
    record = await contract(contracts)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        store=store,
        executor=AppendExecutor(tmp_path / "effects"),
    )
    await gateway.evaluate(call(record), permissions())
    with pytest.raises(asyncio.CancelledError):
        await gateway.execute("request-1")
    assert not (tmp_path / "effects").exists()
    assert (await store.get("requests", "request-1"))["execution_state"] == "READY"
    assert (await gateway.execute("request-1")).status == "EXECUTED"


@pytest.mark.asyncio
async def test_cancelled_replan_still_blocks_the_original_request(tmp_path):
    from ra_agent.contracts import ContractService, TaskContractUpdateRequest
    from ra_agent.gateway import GatewayError, ToolGateway
    from ra_agent.permissions import PermissionResolver
    from tests.unit.core.test_gateway_hardening import call, contract, permissions

    committed = asyncio.Event()
    release = asyncio.Event()

    class PausedContractService(ContractService):
        async def update_contract(self, contract_id, request):
            record = await super().update_contract(contract_id, request)
            committed.set()
            await release.wait()
            return record

    store = CoreStateStore(tmp_path / "state.sqlite3")
    contracts = PausedContractService(store=store)
    record = await contract(contracts)
    gateway = ToolGateway(
        contracts=contracts, resolver=PermissionResolver(), store=store
    )
    await gateway.evaluate(call(record), permissions())
    operation = asyncio.create_task(
        gateway.replan("request-1", TaskContractUpdateRequest(goals=["revised"]))
    )
    await asyncio.wait_for(committed.wait(), timeout=5)
    operation.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    with pytest.raises(GatewayError, match="superseded"):
        await gateway.execute("request-1")


@pytest.mark.asyncio
async def test_read_snapshot_proceeds_while_another_connection_reserves_writer(
    tmp_path,
):
    import sqlite3

    path = tmp_path / "state.sqlite3"
    store = CoreStateStore(path)
    store.set_session("session", {"user_id": "owner"})
    writer = sqlite3.connect(path)
    try:
        writer.execute("BEGIN IMMEDIATE")
        # No wall-clock performance assertion: the read completes before the held
        # writer reservation is released, with a timeout only to bound a deadlock.
        result = await asyncio.wait_for(store.get("sessions", "session"), timeout=2)
        assert result == {"user_id": "owner"}
    finally:
        writer.rollback()
        writer.close()
