"""Intent controls through actual Core file adapters and persistent admission state."""

from pathlib import Path

import pytest
from ra_agent.contracts.core_service import ContractService
from ra_agent.contracts.core_v1 import (
    ContractPermissionRule,
    EffectClass,
    GrantEffect,
    GrantScope,
    PermissionContext,
    PermissionGrant,
    TaskContractCreateRequest,
    TaskContractUpdateRequest,
    ToolCallEnvelope,
)
from ra_agent.gateway import CoreToolExecutor, GatewayError, ToolGateway
from ra_agent.gateway.state import CoreStateStore
from ra_agent.permissions import PermissionResolver
from ra_agent.security.intent import IntentPolicy, IntentRuleGuard
from ra_agent.tools.path_resolver import SafePathResolver


async def setup(tmp_path: Path, policy=None):
    root = tmp_path / "workspace"
    (root / "reports").mkdir(parents=True)
    (root / "inputs").mkdir()
    (root / "inputs/log.txt").write_text("Device: demo; Evidence: timeout=3")
    store = CoreStateStore(tmp_path / "state.sqlite3")
    contracts = ContractService(store=store)
    record = await contracts.create_contract(
        TaskContractCreateRequest(
            session_id="session",
            task_id="task",
            user_id="user",
            goals=["outage report"],
            completion_criteria=[
                "intent:report=reports/report.txt",
                "intent:contains=Evidence:",
                "intent:contains=Device: demo",
            ],
            allowed=[ContractPermissionRule(action="*", resource="*", effect="*")],
            policy_version="policy:1",
            tool_manifest_digest="manifest:1",
        )
    )
    record = await contracts.confirm_contract(
        record.ref.contract_id, version=1, confirmed_by="user"
    )

    def grant(source):
        return PermissionGrant(
            subject="*",
            skill="*",
            tool="*",
            action="*",
            resource="*",
            effect=GrantEffect.ALLOW,
            scope=GrantScope.GLOBAL,
            source=source,
        )

    permissions = PermissionContext(
        user_grants=[grant("user")],
        skill_grants=[grant("skill")],
        system_grants=[grant("system")],
    )
    guard = IntentRuleGuard(store, policy=policy)
    executor = CoreToolExecutor(
        path_resolver=SafePathResolver(
            root, max_path_length=4096, max_read_bytes=4096, max_write_bytes=4096
        ),
        memory_path=tmp_path / "memory.json",
        outbox_path=tmp_path / "outbox.jsonl",
    )
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        store=store,
        executor=executor,
        intent_guard=guard,
    )
    return gateway, record, permissions, root


def call(
    record,
    request_id,
    tool="write_file",
    path="reports/report.txt",
    content="Device: demo\nEvidence: timeout=3",
):
    return ToolCallEnvelope(
        request_id=request_id,
        task_id="task",
        session_id="session",
        contract_ref=record.ref,
        skill_ref="writer",
        tool=tool,
        action=tool,
        canonical_args={
            "path": path,
            **({"content": content} if tool == "write_file" else {}),
        },
        resource=path,
        effect_class=EffectClass.WRITE if tool == "write_file" else EffectClass.READ,
    )


@pytest.mark.asyncio
async def test_approved_report_real_file_and_signed_decision_fields(tmp_path):
    gateway, record, permissions, root = await setup(tmp_path)
    result = await gateway.evaluate(call(record, "normal"), permissions)
    assert result.decision.decision.value == "ALLOW"
    assert result.intent is not None
    assert result.intent.disposition == "CONTINUE"
    await gateway.execute("normal")
    assert "Evidence:" in (root / "reports/report.txt").read_text()
    assert result.decision.versions["detector"] == "bounded-report-rule:1"


@pytest.mark.asyncio
async def test_permission_legal_deviation_stops_before_effect_and_survives_restart(
    tmp_path,
):
    gateway, record, permissions, root = await setup(tmp_path)
    result = await gateway.evaluate(
        call(record, "attack", content="ADVERTISEMENT"), permissions
    )
    assert result.effective_permission.conflict_code is None
    assert result.decision.reason_code.value == "INTENT_DEVIATION"
    assert result.intent is not None
    assert result.intent.disposition == "SAFE_STOP"
    with pytest.raises(GatewayError):
        await gateway.execute("attack")
    assert not (root / "reports/report.txt").exists()
    reopened = CoreStateStore(tmp_path / "state.sqlite3")
    gateway._store = reopened
    gateway.intent_guard = IntentRuleGuard(reopened)
    with pytest.raises(GatewayError, match="INTENT_STOPPED"):
        await gateway.evaluate(call(record, "after-stop"), permissions)
    events = await gateway.event_store.list_task_events("task")
    assert any(e["type"] == "TASK_SAFE_STOPPED" for e in events)
    assert not any(e["type"] == "EXECUTION_STARTED" for e in events)


@pytest.mark.asyncio
async def test_rechecks_and_request_retries_do_not_count_as_extra_steps(tmp_path):
    gateway, record, permissions, root = await setup(tmp_path)
    request = call(record, "read-1", "read_file", "inputs/log.txt")
    for _ in range(4):
        assert (
            await gateway.evaluate(request, permissions)
        ).decision.decision.value == "ALLOW"
    await gateway.execute("read-1")
    await gateway.execute("read-1")
    history = await gateway._store.get("intent_history", "task:1")
    assert len(history) == 1
    second = await gateway.evaluate(
        call(record, "read-2", "read_file", "inputs/log.txt"), permissions
    )
    assert second.intent is not None
    assert second.intent.disposition == "CONTINUE"
    result = await gateway.evaluate(
        call(record, "read-3", "read_file", "inputs/log.txt"), permissions
    )
    assert result.intent is not None
    assert "repeated_read_without_new_input" in result.intent.trigger_dimensions
    assert result.intent is not None
    assert "request:read-1" in result.intent.evidence_refs


@pytest.mark.asyncio
async def test_queued_allow_cannot_execute_after_task_is_stopped(tmp_path):
    gateway, record, permissions, root = await setup(tmp_path)
    await gateway.evaluate(call(record, "queued"), permissions)
    await gateway.evaluate(call(record, "attack", content="fake"), permissions)
    with pytest.raises(GatewayError, match="INTENT_STOPPED"):
        await gateway.execute("queued")
    assert not (root / "reports/report.txt").exists()


@pytest.mark.asyncio
async def test_current_policy_tightening_is_checked_before_execution(tmp_path):
    gateway, record, permissions, root = await setup(tmp_path)
    await gateway.evaluate(
        call(record, "first", "read_file", "inputs/log.txt"), permissions
    )
    await gateway.execute("first")
    await gateway.evaluate(
        call(record, "second", "read_file", "inputs/log.txt"), permissions
    )
    assert gateway.intent_guard is not None
    gateway.intent_guard.policy = IntentPolicy(
        version="intent-policy:2", repeat_limit=2
    )
    with pytest.raises(GatewayError, match="INTENT_DEVIATION"):
        await gateway.execute("second")


@pytest.mark.asyncio
async def test_hard_permission_denial_is_not_attributed_to_intent(tmp_path):
    gateway, record, _, root = await setup(tmp_path)
    result = await gateway.evaluate(call(record, "no-grants"), PermissionContext())
    assert result.decision.decision.value == "DENY"
    assert result.intent is None
    assert result.decision.reason_code.value != "INTENT_DEVIATION"


@pytest.mark.asyncio
async def test_detector_failure_denies_and_terminates_without_side_effect(tmp_path):
    gateway, record, permissions, root = await setup(tmp_path)

    class Broken:
        async def check(self, *args):
            raise RuntimeError("unavailable")

    gateway.intent_guard = Broken()
    result = await gateway.evaluate(call(record, "failure"), permissions)
    assert result.intent is not None
    assert result.intent.disposition == "SAFE_STOP"
    assert result.intent is not None
    assert "intent_check_unavailable" in result.intent.trigger_dimensions
    assert (await gateway._store.get("task_lifecycle", "task"))["status"] == "CANCELLED"
    assert not (root / "reports/report.txt").exists()


@pytest.mark.asyncio
async def test_user_confirmed_new_goal_changes_report_criteria(tmp_path):
    gateway, record, permissions, root = await setup(tmp_path)
    updated = await gateway.contracts.update_contract(
        record.ref.contract_id,
        TaskContractUpdateRequest(
            goals=["maintenance report"],
            completion_criteria=[
                "intent:report=reports/report.txt",
                "intent:contains=maintenance",
            ],
        ),
    )
    updated = await gateway.contracts.confirm_contract(
        updated.ref.contract_id, version=2, confirmed_by="user"
    )
    result = await gateway.evaluate(
        call(updated, "new-goal", content="maintenance"), permissions
    )
    assert result.intent is not None
    assert result.intent.contract_version == 2
    await gateway.execute("new-goal")
    assert (root / "reports/report.txt").read_text() == "maintenance"


@pytest.mark.asyncio
async def test_evicted_request_does_not_read_future_history(tmp_path):
    gateway, record, permissions, root = await setup(tmp_path)
    first = call(record, "old", "read_file", "inputs/log.txt")
    await gateway.evaluate(first, permissions)
    for index in range(10):
        await gateway.evaluate(
            call(record, f"later-{index}", "read_file", f"inputs/{index}.txt"),
            permissions,
        )
    result = await gateway.evaluate(first, permissions)
    assert result.intent is not None
    assert result.intent.step_index == 0
    assert result.intent is not None
    assert not result.intent.evidence_refs
    assert len(await gateway._store.get("intent_history", "task:1")) == 8
