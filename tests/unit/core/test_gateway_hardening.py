from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from ra_agent.contracts import (
    ConfirmationPolicyV1,
    ContractPermissionRule,
    ContractService,
    EffectClass,
    GatewayDecisionType,
    GrantEffect,
    GrantScope,
    PermissionContext,
    PermissionGrant,
    TaskContractCreateRequest,
    ToolCallEnvelope,
)
from ra_agent.gateway import GatewayError, ToolGateway
from ra_agent.permissions import PermissionResolver


class AppendExecutor:
    """Real durable side effect with controllable asynchronous completion/failure."""

    def __init__(self, path: Path, *, pause: bool = False, fail: bool = False):
        self.path = path
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.pause = pause
        self.fail = fail

    async def execute(self, envelope, *, gateway_token=None):
        with self.path.open("a", encoding="utf-8") as output:
            output.write(envelope.canonical_args["content"] + "\n")
        self.started.set()
        if self.pause:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("failed after side effect")
        return {"content": envelope.canonical_args["content"]}


async def contract(service, *, confirmation=False):
    draft = await service.create_contract(
        TaskContractCreateRequest(
            session_id="session-1",
            task_id="task-1",
            user_id="user-1",
            goals=["write approved content"],
            allowed=[
                ContractPermissionRule(
                    tool="create_file", action="create_file", resource="reports/**", effect="WRITE"
                )
            ],
            confirmation=ConfirmationPolicyV1(
                required_actions=["create_file"] if confirmation else []
            ),
            policy_version="policy:1",
            tool_manifest_digest="tools:1",
        )
    )
    return await service.confirm_contract(
        draft.contract.contract_id, version=1, confirmed_by="user-1"
    )


def permissions(*, one_shot=False):
    def grant(source):
        return PermissionGrant(
            subject="*",
            skill="*",
            tool="create_file",
            action="create_file",
            resource="reports/**",
            effect=GrantEffect.ALLOW,
            source=source,
            scope=GrantScope.ONE_SHOT if one_shot else GrantScope.GLOBAL,
            scope_ref="request-1" if one_shot else None,
        )

    return PermissionContext(
        user_grants=[grant("user")], skill_grants=[grant("skill")], system_grants=[grant("system")]
    )


def call(record):
    return ToolCallEnvelope(
        request_id="request-1",
        task_id="task-1",
        session_id="session-1",
        contract_ref=record.ref,
        skill_ref="writer",
        tool="create_file",
        action="create_file",
        canonical_args={"content": "approved"},
        resource="reports/a.md",
        effect_class=EffectClass.WRITE,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"canonical_args": {"content": "substituted"}},
        {"resource": "reports/b.md"},
        {"task_id": "task-2"},
        {"session_id": "session-2"},
        {"skill_ref": "other"},
        {"tool": "memory_write"},
        {"action": "delete_file"},
        {"effect_class": EffectClass.DELETE},
    ],
)
async def test_request_id_cannot_replace_the_approved_envelope(tmp_path, change):
    contracts = ContractService()
    record = await contract(contracts, confirmation=True)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        executor=AppendExecutor(tmp_path / "effects"),
    )
    envelope = call(record)
    await gateway.evaluate(envelope, permissions())
    with pytest.raises(GatewayError, match="different|bound|fingerprint"):
        await gateway.evaluate(envelope.model_copy(update=change), permissions())
    await gateway.resume_after_confirmation("request-1", confirmed=True)
    await gateway.execute("request-1")
    assert (tmp_path / "effects").read_text() == "approved\n"


@pytest.mark.asyncio
async def test_caller_mutation_cannot_change_pending_or_allowed_snapshot(tmp_path):
    contracts = ContractService()
    record = await contract(contracts, confirmation=True)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        executor=AppendExecutor(tmp_path / "effects"),
    )
    envelope = call(record)
    await gateway.evaluate(envelope, permissions())
    envelope.canonical_args["content"] = "substituted"
    await gateway.resume_after_confirmation("request-1", confirmed=True)
    await gateway.execute("request-1")
    assert (tmp_path / "effects").read_text() == "approved\n"


@pytest.mark.asyncio
async def test_successful_one_shot_retry_returns_cached_result_without_side_effects(tmp_path):
    contracts = ContractService()
    record = await contract(contracts)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        executor=AppendExecutor(tmp_path / "effects"),
    )
    await gateway.evaluate(call(record), permissions(one_shot=True))
    first = await gateway.execute("request-1")
    first.result["content"] = "caller mutation"
    repeated = await gateway.execute("request-1")
    assert repeated.result == {"content": "approved"}
    assert (tmp_path / "effects").read_text() == "approved\n"
    assert len(await gateway.event_store.list_task_events("task-1")) == 6


@pytest.mark.asyncio
async def test_concurrent_execution_cannot_duplicate_a_side_effect(tmp_path):
    contracts = ContractService()
    record = await contract(contracts)
    executor = AppendExecutor(tmp_path / "effects", pause=True)
    gateway = ToolGateway(contracts=contracts, resolver=PermissionResolver(), executor=executor)
    await gateway.evaluate(call(record), permissions())
    pending = asyncio.create_task(gateway.execute("request-1"))
    await executor.started.wait()
    try:
        with pytest.raises(GatewayError, match="UNKNOWN|in progress"):
            await asyncio.wait_for(gateway.execute("request-1"), timeout=1)
    finally:
        executor.release.set()
        await pending
    assert (tmp_path / "effects").read_text() == "approved\n"


@pytest.mark.asyncio
async def test_failure_after_a_side_effect_is_unknown_and_never_reexecuted(tmp_path):
    contracts = ContractService()
    record = await contract(contracts)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        executor=AppendExecutor(tmp_path / "effects", fail=True),
    )
    await gateway.evaluate(call(record), permissions())
    with pytest.raises(RuntimeError, match="failed after side effect"):
        await gateway.execute("request-1")
    with pytest.raises(GatewayError, match="UNKNOWN"):
        await gateway.execute("request-1")
    assert (tmp_path / "effects").read_text() == "approved\n"


@pytest.mark.asyncio
async def test_contract_confirmation_and_request_survive_restart(tmp_path):
    from ra_agent.confirmations import ConfirmationService
    from ra_agent.gateway.state import CoreStateStore

    database = tmp_path / "core.sqlite3"
    first_store = CoreStateStore(database)
    contracts = ContractService(store=first_store)
    record = await contract(contracts, confirmation=True)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        store=first_store,
        confirmation_service=ConfirmationService(store=first_store),
        executor=AppendExecutor(tmp_path / "effects"),
    )
    evaluated = await gateway.evaluate(call(record), permissions())
    second_store = CoreStateStore(database)
    restarted_contracts = ContractService(store=second_store)
    restarted = ToolGateway(
        contracts=restarted_contracts,
        resolver=PermissionResolver(),
        store=second_store,
        confirmation_service=ConfirmationService(store=second_store),
        executor=AppendExecutor(tmp_path / "effects"),
    )
    assert await restarted_contracts.get_active_contract(record.contract.contract_id) == record
    assert evaluated.decision.confirmation_id is not None
    approved = await restarted.resolve_confirmation(
        evaluated.decision.confirmation_id, confirmed=True, resolved_by="user-1"
    )
    assert approved.decision.decision is GatewayDecisionType.ALLOW
    await restarted.execute("request-1")
    assert (tmp_path / "effects").read_text() == "approved\n"
    third_store = CoreStateStore(database)
    replay = ToolGateway(
        contracts=ContractService(store=third_store),
        resolver=PermissionResolver(),
        store=third_store,
        confirmation_service=ConfirmationService(store=third_store),
        executor=AppendExecutor(tmp_path / "effects"),
    )
    assert (await replay.execute("request-1")).result == {"content": "approved"}
    assert (tmp_path / "effects").read_text() == "approved\n"


@pytest.mark.asyncio
async def test_independent_persistent_gateways_share_atomic_execution_claim(tmp_path):
    from ra_agent.gateway.state import CoreStateStore

    database = tmp_path / "core.sqlite3"
    contracts = ContractService(store=CoreStateStore(database))
    record = await contract(contracts)
    executor = AppendExecutor(tmp_path / "effects", pause=True)
    first = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        store=CoreStateStore(database),
        executor=executor,
    )
    second = ToolGateway(
        contracts=ContractService(store=CoreStateStore(database)),
        resolver=PermissionResolver(),
        store=CoreStateStore(database),
        executor=AppendExecutor(tmp_path / "effects"),
    )
    await first.evaluate(call(record), permissions())
    pending = asyncio.create_task(first.execute("request-1"))
    await executor.started.wait()
    try:
        with pytest.raises(GatewayError, match="UNKNOWN"):
            await second.execute("request-1")
    finally:
        executor.release.set()
        await pending
    assert (await second.execute("request-1")).result == {"content": "approved"}
    assert (tmp_path / "effects").read_text() == "approved\n"


@pytest.mark.asyncio
async def test_revocation_during_recheck_audit_cannot_reach_the_executor(tmp_path):
    from ra_agent.gateway.fakes import InMemoryEventStore

    class RevokingEventStore(InMemoryEventStore):
        gateway: ToolGateway | None = None

        async def append_event(self, event):
            if (
                event["type"] == "GATEWAY_ALLOWED"
                and len(await self.list_task_events("task-1")) >= 2
            ):
                assert self.gateway is not None
                await self.gateway.replace_permission_context("request-1", PermissionContext())
            return await super().append_event(event)

    contracts = ContractService()
    record = await contract(contracts)
    events = RevokingEventStore()
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        event_store=events,
        executor=AppendExecutor(tmp_path / "effects"),
    )
    events.gateway = gateway
    await gateway.evaluate(call(record), permissions())
    with pytest.raises(GatewayError, match="authorization changed|DENY"):
        await gateway.execute("request-1")
    assert not (tmp_path / "effects").exists()


@pytest.mark.asyncio
async def test_contract_replaced_during_execution_started_audit_is_rechecked(tmp_path):
    from ra_agent.contracts import TaskContractUpdateRequest
    from ra_agent.gateway.fakes import InMemoryEventStore

    contracts = ContractService()
    record = await contract(contracts)

    class UpdatingEventStore(InMemoryEventStore):
        async def append_event(self, event):
            if event["type"] == "EXECUTION_STARTED":
                draft = await contracts.update_contract(
                    record.contract.contract_id,
                    TaskContractUpdateRequest(goals=["changed authorization"]),
                )
                await contracts.confirm_contract(
                    record.contract.contract_id,
                    version=draft.contract.version,
                    confirmed_by="user-1",
                )
            return await super().append_event(event)

    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        event_store=UpdatingEventStore(),
        executor=AppendExecutor(tmp_path / "effects"),
    )
    await gateway.evaluate(call(record), permissions())
    with pytest.raises(GatewayError, match="VERSION_STALE"):
        await gateway.execute("request-1")
    assert not (tmp_path / "effects").exists()


@pytest.mark.asyncio
async def test_server_permission_revocation_during_audit_is_rechecked(tmp_path):
    from ra_agent.gateway.fakes import InMemoryEventStore

    authority = permissions()

    async def provider(envelope):
        return authority

    class RevokingEventStore(InMemoryEventStore):
        async def append_event(self, event):
            nonlocal authority
            if event["type"] == "EXECUTION_STARTED":
                authority = PermissionContext()
            return await super().append_event(event)

    contracts = ContractService()
    record = await contract(contracts)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        permission_provider=provider,
        event_store=RevokingEventStore(),
        executor=AppendExecutor(tmp_path / "effects"),
    )
    await gateway.evaluate(call(record), permissions())
    with pytest.raises(GatewayError, match="USER_DENY"):
        await gateway.execute("request-1")
    assert not (tmp_path / "effects").exists()


@pytest.mark.asyncio
async def test_cancelled_side_effect_stays_unknown_after_restart(tmp_path):
    from ra_agent.gateway.state import CoreStateStore

    database = tmp_path / "core.sqlite3"
    store = CoreStateStore(database)
    contracts = ContractService(store=store)
    record = await contract(contracts)
    executor = AppendExecutor(tmp_path / "effects", pause=True)
    first = ToolGateway(
        contracts=contracts, resolver=PermissionResolver(), store=store, executor=executor
    )
    await first.evaluate(call(record), permissions(one_shot=True))
    pending = asyncio.create_task(first.execute("request-1"))
    await executor.started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    restarted = ToolGateway(
        contracts=ContractService(store=CoreStateStore(database)),
        resolver=PermissionResolver(),
        store=CoreStateStore(database),
        executor=AppendExecutor(tmp_path / "effects"),
    )
    with pytest.raises(GatewayError, match="UNKNOWN"):
        await restarted.execute("request-1")
    assert (tmp_path / "effects").read_text() == "approved\n"


@pytest.mark.asyncio
async def test_killed_process_claim_is_not_reexecuted_after_restart(tmp_path):
    import os
    import subprocess
    import sys

    from ra_agent.gateway.state import CoreStateStore

    database = tmp_path / "core.sqlite3"
    store = CoreStateStore(database)
    contracts = ContractService(store=store)
    record = await contract(contracts)
    gateway = ToolGateway(contracts=contracts, resolver=PermissionResolver(), store=store)
    await gateway.evaluate(call(record), permissions())
    script = """
import asyncio, os, sys
from pathlib import Path
from ra_agent.gateway import ToolGateway
from ra_agent.gateway.state import CoreStateStore
from ra_agent.contracts import ContractService
from ra_agent.permissions import PermissionResolver
class CrashExecutor:
    async def execute(self, envelope, *, gateway_token=None):
        with Path(sys.argv[2]).open('a') as output:
            output.write('approved\\n')
        os._exit(23)
store = CoreStateStore(sys.argv[1])
gateway = ToolGateway(contracts=ContractService(store=store), resolver=PermissionResolver(),
                      store=store, executor=CrashExecutor())
asyncio.run(gateway.execute('request-1'))
"""
    environment = dict(os.environ, PYTHONUTF8="1")
    outcome = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script, str(database), str(tmp_path / "effects")],
        env=environment,
        capture_output=True,
        timeout=15,
    )
    assert outcome.returncode == 23, outcome.stderr.decode(errors="replace")
    restarted = ToolGateway(
        contracts=ContractService(store=CoreStateStore(database)),
        resolver=PermissionResolver(),
        store=CoreStateStore(database),
        executor=AppendExecutor(tmp_path / "effects"),
    )
    with pytest.raises(GatewayError, match="UNKNOWN"):
        await restarted.execute("request-1")
    assert (tmp_path / "effects").read_text() == "approved\n"


@pytest.mark.asyncio
async def test_persistent_replan_and_revoked_permissions_cannot_be_reset_by_reevaluation(tmp_path):
    from ra_agent.contracts import TaskContractUpdateRequest
    from ra_agent.gateway.state import CoreStateStore

    database = tmp_path / "core.sqlite3"
    store = CoreStateStore(database)
    contracts = ContractService(store=store)
    record = await contract(contracts)
    first = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        store=store,
        executor=AppendExecutor(tmp_path / "effects"),
    )
    await first.evaluate(call(record), permissions())
    await first.replace_permission_context("request-1", PermissionContext())
    restarted = ToolGateway(
        contracts=ContractService(store=CoreStateStore(database)),
        resolver=PermissionResolver(),
        store=CoreStateStore(database),
        executor=AppendExecutor(tmp_path / "effects"),
    )
    result = await restarted.evaluate(call(record), permissions())
    assert result.decision.decision is GatewayDecisionType.DENY
    with pytest.raises(GatewayError):
        await restarted.execute("request-1")
    await restarted.replan("request-1", TaskContractUpdateRequest(goals=["new authorization"]))
    final = ToolGateway(
        contracts=ContractService(store=CoreStateStore(database)),
        resolver=PermissionResolver(),
        store=CoreStateStore(database),
        executor=AppendExecutor(tmp_path / "effects"),
    )
    with pytest.raises(GatewayError, match="superseded"):
        await final.execute("request-1")
    assert not (tmp_path / "effects").exists()


@pytest.mark.asyncio
async def test_two_threaded_sqlite_gateways_never_repeat_side_effect(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from ra_agent.gateway.state import CoreStateStore

    database = tmp_path / "core.sqlite3"
    store = CoreStateStore(database)
    contracts = ContractService(store=store)
    record = await contract(contracts)
    gateway = ToolGateway(contracts=contracts, resolver=PermissionResolver(), store=store)
    await gateway.evaluate(call(record), permissions())
    barrier = Barrier(2)

    def execute():
        isolated_store = CoreStateStore(database)
        isolated = ToolGateway(
            contracts=ContractService(store=isolated_store),
            resolver=PermissionResolver(),
            store=isolated_store,
            executor=AppendExecutor(tmp_path / "effects"),
        )
        barrier.wait(timeout=5)
        try:
            return asyncio.run(isolated.execute("request-1")).status
        except GatewayError as error:
            return str(error)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute) for _ in range(2)]
        outcomes = [future.result(timeout=10) for future in futures]
    assert "EXECUTED" in outcomes
    assert all(item == "EXECUTED" or "UNKNOWN" in item for item in outcomes)
    assert (tmp_path / "effects").read_text() == "approved\n"


@pytest.mark.asyncio
async def test_caller_grants_only_narrow_server_authority_and_preserve_one_shot(tmp_path):
    from datetime import UTC, datetime, timedelta

    authority = permissions()
    for grant in authority.user_grants + authority.skill_grants + authority.system_grants:
        grant.limits = {"max_bytes": 100}

    async def provider(envelope):
        return authority

    contracts = ContractService()
    record = await contract(contracts)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        permission_provider=provider,
        executor=AppendExecutor(tmp_path / "effects"),
    )
    caller = permissions(one_shot=True)
    expiry = datetime.now(UTC) + timedelta(minutes=1)
    caller.user_grants[0].limits = {"max_bytes": 5}
    caller.user_grants[0].expires_at = expiry
    result = await gateway.evaluate(call(record), caller)
    assert result.decision.decision is GatewayDecisionType.ALLOW
    assert result.effective_permission.constraints["max_bytes"] == 5
    assert result.effective_permission.constraints["earliest_expiry"] == expiry.isoformat()
    assert any(grant.scope is GrantScope.ONE_SHOT for grant in result.effective_permission.allowed)
    caller.system_grants[0].effect = GrantEffect.DENY
    await gateway.replace_permission_context("request-1", caller)
    with pytest.raises(GatewayError, match="SYSTEM_DENY"):
        await gateway.execute("request-1")
    assert not (tmp_path / "effects").exists()


@pytest.mark.asyncio
async def test_caller_allow_never_overrides_server_deny(tmp_path):
    authority = permissions()
    authority.system_grants[0].effect = GrantEffect.DENY

    async def provider(envelope):
        return authority

    contracts = ContractService()
    record = await contract(contracts)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        permission_provider=provider,
        executor=AppendExecutor(tmp_path / "effects"),
    )
    result = await gateway.evaluate(call(record), permissions())
    assert result.decision.decision is GatewayDecisionType.DENY
    assert result.decision.reason_code.value == "SYSTEM_DENY"
    with pytest.raises(GatewayError):
        await gateway.execute("request-1")
    assert not (tmp_path / "effects").exists()


@pytest.mark.asyncio
async def test_completed_provider_request_replay_does_not_repeat_audit(tmp_path):
    async def provider(envelope):
        return permissions()

    contracts = ContractService()
    record = await contract(contracts, confirmation=True)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        permission_provider=provider,
        executor=AppendExecutor(tmp_path / "effects"),
    )
    original = await gateway.evaluate(call(record), PermissionContext())
    assert original.decision.confirmation_id is not None
    approved = await gateway.resolve_confirmation(
        original.decision.confirmation_id, confirmed=True, resolved_by="user-1"
    )
    await gateway.execute("request-1")
    count = len(await gateway.event_store.list_task_events("task-1"))
    assert await gateway.evaluate(call(record), PermissionContext()) == approved
    assert (
        await gateway.resolve_confirmation(
            original.decision.confirmation_id, confirmed=True, resolved_by="user-1"
        )
        == approved
    )
    assert len(await gateway.event_store.list_task_events("task-1")) == count
    assert (tmp_path / "effects").read_text() == "approved\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("approved", [True, False])
async def test_evaluate_retry_preserves_the_resolved_confirmation(tmp_path, approved):
    async def provider(envelope):
        return permissions()

    contracts = ContractService()
    record = await contract(contracts, confirmation=True)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        permission_provider=provider,
        executor=AppendExecutor(tmp_path / "effects"),
    )
    first = await gateway.evaluate(call(record), PermissionContext())
    assert first.decision.confirmation_id is not None
    await gateway.resolve_confirmation(
        first.decision.confirmation_id, confirmed=approved, resolved_by="user-1"
    )
    retried = await gateway.evaluate(call(record), PermissionContext())
    expected = GatewayDecisionType.ALLOW if approved else GatewayDecisionType.DENY
    assert retried.decision.decision is expected
    assert retried.decision.confirmation_id == first.decision.confirmation_id


@pytest.mark.asyncio
async def test_gateway_uses_optional_event_tail_without_replaying_task_history(tmp_path):
    from ra_agent.gateway.fakes import InMemoryEventStore

    class TailOnlyStore(InMemoryEventStore):
        async def get_last_event_id(self, task_id):
            events = self._events.get(task_id, [])
            return events[-1]["event_id"] if events else None

        async def list_task_events(self, task_id):
            pytest.fail("Appending an event must use the available constant-time tail lookup")

    contracts = ContractService()
    record = await contract(contracts)
    events = TailOnlyStore()
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        event_store=events,
        executor=AppendExecutor(tmp_path / "effects"),
    )
    await gateway.evaluate(call(record), permissions())
    await gateway.execute("request-1")
    chain = await InMemoryEventStore.list_task_events(events, "task-1")
    assert len(chain) == 6
    assert chain[0]["parent_event_id"] is None
    assert [event["parent_event_id"] for event in chain[1:]] == [
        event["event_id"] for event in chain[:-1]
    ]
