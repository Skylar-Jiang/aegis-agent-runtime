"""Regression cases from the real execution review, using isolated resources."""

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from ra_agent.contracts import (
    ContractPermissionRule,
    ContractService,
    TaskContractUpdateRequest,
)
from ra_agent.database.workbench_store import DEFAULT_PROFILE
from ra_agent.gateway import GatewayError, ToolGateway
from ra_agent.gateway.state import CoreStateStore
from ra_agent.permissions import PermissionResolver
from tests.integration.test_core_http_hardening import _app
from tests.integration.test_core_http_hardening import _body
from tests.unit.core.test_core_permission_resolver import _context, _contract, _envelope
from tests.unit.core.test_gateway_hardening import (
    AppendExecutor,
    call,
    contract,
    permissions,
)


@pytest.mark.parametrize("seed_from_history", [False, True])
def test_profile_task_budget_cannot_reset_by_changing_tool_or_skill(
    tmp_path, seed_from_history
):
    app = _app(tmp_path)
    with TestClient(app) as client:
        body = _body(client)
        cid = body["envelope"]["contract_ref"]["contract_id"]
        client.post(
            f"/api/v1/contracts/{cid}/versions",
            json={
                "allowed": [
                    {
                        "tool": "*",
                        "action": "*",
                        "resource": "reports/**",
                        "effect": "WRITE",
                    }
                ],
                "limits": {"max_affected_objects": 100},
            },
        ).raise_for_status()
        body["envelope"]["contract_ref"] = client.post(
            f"/api/v1/contracts/{cid}/confirm",
            json={"version": 2, "confirmed_by": "user-http"},
        ).json()["data"]["ref"]
        client.post("/api/v1/tool-calls/evaluate", json=body).raise_for_status()
        client.post("/api/v1/tool-calls/request-http/execute").raise_for_status()
        if seed_from_history:
            # Model upgrade from a version without the task-total counter.
            state = app.state.core_state_store
            with state.transaction() as tx:
                if tx._connection is None:
                    for key in list(tx._memory):
                        if key[0] == "quotas":
                            tx._memory.pop(key)
                else:
                    tx._connection.execute(
                        "DELETE FROM core_state WHERE namespace='quotas'"
                    )
        profile = deepcopy(DEFAULT_PROFILE)
        profile["max_affected_objects"] = 1
        client.put("/api/security-profiles/default", json=profile).raise_for_status()
        for index, (tool, skill) in enumerate(
            [("create_file", "writer"), ("write_file", "core-ui")]
        ):
            request_id = f"switch-{index}"
            path = f"reports/switch-{index}.txt"
            body["envelope"].update(
                request_id=request_id,
                tool=tool,
                action=tool,
                skill_ref=skill,
                resource=path,
                canonical_args={"path": path, "content": "denied"},
            )
            client.post("/api/v1/tool-calls/evaluate", json=body).raise_for_status()
            result = client.post(f"/api/v1/tool-calls/{request_id}/execute")
            assert result.status_code == 409, result.text
            assert "LIMIT_EXCEEDED" in result.text
            assert not (tmp_path / "workspace" / path).exists()


def test_file_deny_uses_filesystem_case_semantics():
    boundary = _contract()
    boundary.denied = [
        ContractPermissionRule(
            tool="create_file",
            action="create_file",
            resource="reports/private.md",
            effect="WRITE",
        )
    ]
    result = PermissionResolver(
        case_insensitive_files=True
    ).resolve_effective_permission(
        _envelope("reports/PRIVATE.MD"), boundary, _context()
    )
    assert result.conflict_code is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("limits", [{"max_affected_objects": 0}, {"max_bytes": 1}])
async def test_contract_limits_prevent_real_side_effect(tmp_path, limits):
    store = CoreStateStore(tmp_path / "state.sqlite3")
    contracts = ContractService(store=store)
    original = await contract(contracts)
    updated = await contracts.update_contract(
        original.ref.contract_id, TaskContractUpdateRequest(limits=limits)
    )
    record = await contracts.confirm_contract(
        updated.ref.contract_id, version=2, confirmed_by="user-1"
    )
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        store=store,
        executor=AppendExecutor(tmp_path / "effects"),
    )
    await gateway.evaluate(call(record), permissions())
    with pytest.raises(GatewayError, match="LIMIT|limit|quota"):
        await gateway.execute("request-1")
    assert not (tmp_path / "effects").exists()


@pytest.mark.asyncio
async def test_quota_is_atomic_and_survives_new_gateway(tmp_path):
    reservations_finished = asyncio.Event()
    reservation_count = 0

    class ConcurrentReservationStore(CoreStateStore):
        async def run(self, operation, *, readonly=False):
            nonlocal reservation_count
            if operation.__name__ != "admit_execution":
                return await super().run(operation, readonly=readonly)
            failure = None
            result = None
            try:
                result = await super().run(operation, readonly=readonly)
            except GatewayError as error:
                failure = error
            reservation_count += 1
            if reservation_count == 2:
                reservations_finished.set()
            # A failed reservation must not reset its claim until the other
            # worker has seen it. This deterministically exercises the CI race.
            await asyncio.wait_for(reservations_finished.wait(), timeout=5)
            if failure is not None:
                raise failure
            return result

    path = tmp_path / "state.sqlite3"
    store = CoreStateStore(path)
    contracts = ContractService(store=store)
    record = await contract(contracts)
    context = permissions()
    context.user_grants[0].limits = {"max_affected_objects": 1}
    gateways = [
        ToolGateway(
            contracts=contracts,
            resolver=PermissionResolver(),
            store=ConcurrentReservationStore(path),
            executor=AppendExecutor(tmp_path / "effects"),
        )
        for _ in range(2)
    ]
    for index, gateway in enumerate(gateways):
        envelope = call(record).model_copy(
            update={"request_id": f"request-{index}", "resource": f"reports/{index}.md"}
        )
        await gateway.evaluate(envelope, context)
    # Both requests must hold their pre-admission claims before either reserves
    # quota. A claim from the current protocol has not consumed capacity yet.
    both_claimed = asyncio.Event()
    waiting = 0

    async def concurrent_authority(envelope):
        nonlocal waiting
        waiting += 1
        if waiting >= 2:
            both_claimed.set()
        await asyncio.wait_for(both_claimed.wait(), timeout=5)
        return context

    for gateway in gateways:
        gateway.permission_provider = concurrent_authority
    outcomes = await asyncio.gather(
        *(
            gateway.execute(f"request-{index}")
            for index, gateway in enumerate(gateways)
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, BaseException) for item in outcomes) == 1, outcomes
    assert (tmp_path / "effects").read_text() == "approved\n"
    winner = next(
        i for i, item in enumerate(outcomes) if not isinstance(item, BaseException)
    )
    restarted = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        store=CoreStateStore(path),
        executor=AppendExecutor(tmp_path / "effects"),
    )
    await restarted.execute(f"request-{winner}")
    assert (tmp_path / "effects").read_text() == "approved\n"


def test_cancelled_core_task_cannot_write_and_history_has_current_status(tmp_path):
    app = _app(tmp_path)
    with TestClient(app) as client:
        session = client.post("/api/v1/sessions", json={"user_id": "owner"}).json()[
            "data"
        ]
        task = client.post(
            f"/api/v1/sessions/{session['session_id']}/tasks",
            json={"objective": "write a bounded report"},
        ).json()["data"]
        ref = client.post(
            f"/api/v1/contracts/{task['contract']['ref']['contract_id']}/confirm",
            json={"version": 1, "confirmed_by": "owner"},
        ).json()["data"]["ref"]
        task_id = task["task_id"]
        assert client.get(f"/api/tasks/{task_id}").json()["data"]["status"] == "READY"
        body = {
            "envelope": {
                "request_id": "cancel-call",
                "task_id": task_id,
                "session_id": session["session_id"],
                "contract_ref": ref,
                "skill_ref": "writer",
                "tool": "write_file",
                "action": "write_file",
                "effect_class": "WRITE",
                "resource": "reports/cancelled.txt",
                "canonical_args": {
                    "path": "reports/cancelled.txt",
                    "content": "must not write",
                },
            },
            "permissions": {"user_grants": [], "skill_grants": [], "system_grants": []},
        }
        assert (
            client.post("/api/v1/tool-calls/evaluate", json=body).json()["data"][
                "decision"
            ]["decision"]
            == "ALLOW"
        )
        assert client.post(f"/api/tasks/{task_id}/cancel").status_code == 200
        assert client.post("/api/v1/tool-calls/cancel-call/execute").status_code == 409
        assert not (tmp_path / "workspace/reports/cancelled.txt").exists()


@pytest.mark.asyncio
async def test_profile_changes_wait_for_admitted_operation(tmp_path):
    app = _app(tmp_path)
    await app.state.security_profile_store.ensure_default()
    profile = deepcopy(DEFAULT_PROFILE)
    profile["allowed_actions"] = ["read_file"]
    async with app.state.core_admission_guard():
        update = asyncio.create_task(
            app.state.security_profile_store.create_version("default", profile)
        )
        await asyncio.sleep(0.03)
        assert not update.done()
    saved = await asyncio.wait_for(update, 3)
    assert saved["allowed_actions"] == ["read_file"]


@pytest.mark.skipif(os.name != "nt", reason="real Windows filesystem case semantics")
def test_http_file_deny_applies_to_alternate_case(tmp_path):
    app = _app(tmp_path)
    (tmp_path / "workspace/reports/private.txt").write_text("private data")
    with TestClient(app) as client:
        body = _body(
            client, tool="read_file", action="read_file", effect="READ", skill="core-ui"
        )
        cid = body["envelope"]["contract_ref"]["contract_id"]
        client.post(
            f"/api/v1/contracts/{cid}/versions",
            json={
                "allowed": [
                    {
                        "tool": "read_file",
                        "action": "read_file",
                        "resource": "reports/**",
                        "effect": "READ",
                    }
                ],
                "denied": [
                    {
                        "tool": "read_file",
                        "action": "read_file",
                        "resource": "reports/private.txt",
                        "effect": "READ",
                    }
                ],
            },
        ).raise_for_status()
        body["envelope"]["contract_ref"] = client.post(
            f"/api/v1/contracts/{cid}/confirm",
            json={"version": 2, "confirmed_by": "user-http"},
        ).json()["data"]["ref"]
        body["envelope"].update(
            resource="reports/PRIVATE.TXT",
            canonical_args={"path": "reports/PRIVATE.TXT"},
        )
        result = client.post("/api/v1/tool-calls/evaluate", json=body)
        assert result.json()["data"]["decision"]["decision"] == "DENY"
        assert client.post("/api/v1/tool-calls/request-http/execute").status_code == 409


def test_policy_update_acknowledgement_orders_with_real_commit(tmp_path, monkeypatch):
    app = _app(tmp_path)
    reached, release, updating = threading.Event(), threading.Event(), threading.Event()
    original = app.state.core_policy._skill_tools
    calls = 0

    def pause_final_read(skill):
        nonlocal calls
        calls += 1
        if calls == 3:
            reached.set()
            assert release.wait(5)
        return original(skill)

    monkeypatch.setattr(app.state.core_policy, "_skill_tools", pause_final_read)
    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as pool:
        body = _body(client)
        assert (
            client.post("/api/v1/tool-calls/evaluate", json=body).json()["data"][
                "decision"
            ]["decision"]
            == "ALLOW"
        )
        running = pool.submit(client.post, "/api/v1/tool-calls/request-http/execute")
        assert reached.wait(5)

        def update():
            updating.set()
            profile = deepcopy(DEFAULT_PROFILE)
            profile["allowed_actions"] = ["read_file"]
            return client.put("/api/security-profiles/default", json=profile)

        change = pool.submit(update)
        assert updating.wait(5)
        try:
            assert not change.done()
        finally:
            release.set()
        running.result(timeout=10).raise_for_status()
        change.result(timeout=10).raise_for_status()
        assert (tmp_path / "workspace/reports/a.txt").read_text() == "hello"
        body["envelope"]["request_id"] = "after-policy-change"
        assert (
            client.post("/api/v1/tool-calls/evaluate", json=body).json()["data"][
                "decision"
            ]["decision"]
            == "DENY"
        )
        assert (
            client.post("/api/v1/tool-calls/after-policy-change/execute").status_code
            == 409
        )
