from __future__ import annotations

from pathlib import Path

import pytest

from ra_agent.confirmations import ConfirmationService
from ra_agent.contracts import (
    ConfirmationPolicyV1,
    ContractPermissionRule,
    ContractService,
    ContractVersionRef,
    ContractVersionStatus,
    EffectClass,
    GatewayDecisionType,
    GrantEffect,
    GrantScope,
    PermissionContext,
    PermissionGrant,
    TaskContractCreateRequest,
    TaskContractUpdateRequest,
    ToolCallEnvelope,
)
from ra_agent.events import JsonlEventStore
from ra_agent.audit import HashChain
from ra_agent.gateway import AdapterBypassError, CoreToolExecutor, GatewayError, ToolGateway
from ra_agent.permissions import PermissionResolver
from ra_agent.tools.path_resolver import SafePathResolver


def _context(*, system_effect: GrantEffect = GrantEffect.ALLOW) -> PermissionContext:
    def grant(source: str, *, subject: str = "*", skill: str = "*", effect=GrantEffect.ALLOW):
        return PermissionGrant(
            subject=subject,
            skill=skill,
            tool="create_file",
            action="create_file",
            resource="reports/**",
            effect=effect,
            scope=GrantScope.GLOBAL,
            source=source,
        )
    return PermissionContext(
        user_grants=[grant("user", subject="user-1")],
        skill_grants=[grant("skill", skill="writer")],
        system_grants=[grant("system", effect=system_effect)],
    )


async def _contract(service: ContractService, *, confirm_required: bool = False):
    created = await service.create_contract(
        TaskContractCreateRequest(
            session_id="session-1",
            task_id="task-1",
            user_id="user-1",
            goals=["write report"],
            allowed=[ContractPermissionRule(tool="create_file", action="create_file", resource="reports/**", effect="WRITE")],
            confirmation=ConfirmationPolicyV1(required_actions=["create_file"] if confirm_required else []),
            policy_version="policy:1",
            tool_manifest_digest="tools:1",
        )
    )
    return await service.confirm_contract(created.contract.contract_id, version=1, confirmed_by="user-1")


def _call(record, request_id="request-1") -> ToolCallEnvelope:
    return ToolCallEnvelope(
        request_id=request_id,
        task_id="task-1",
        session_id="session-1",
        contract_ref=record.ref,
        skill_ref="writer",
        tool="create_file",
        action="create_file",
        canonical_args={"path": "reports/a.md", "content": "hello"},
        resource="reports/a.md",
        effect_class=EffectClass.WRITE,
    )


def _executor(tmp_path: Path) -> CoreToolExecutor:
    root = tmp_path / "workspace"
    root.mkdir(parents=True)
    (root / "reports").mkdir(exist_ok=True)
    return CoreToolExecutor(
        path_resolver=SafePathResolver(root, max_path_length=4096, max_read_bytes=1024 * 1024, max_write_bytes=1024 * 1024),
        memory_path=tmp_path / "memory.json",
        outbox_path=tmp_path / "outbox.jsonl",
    )


@pytest.mark.asyncio
async def test_confirmation_closed_loop_rechecks_and_executes_real_file(tmp_path: Path) -> None:
    contracts = ContractService()
    record = await _contract(contracts, confirm_required=True)
    store = JsonlEventStore(tmp_path / "events.jsonl", chain_factory=HashChain)
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        event_store=store,
        confirmation_service=ConfirmationService(),
        executor=_executor(tmp_path),
    )
    first = await gateway.evaluate(_call(record), _context())
    assert first.decision.decision is GatewayDecisionType.REQUIRE_CONFIRMATION
    confirmation_id = first.decision.confirmation_id
    assert confirmation_id

    approved = await gateway.resolve_confirmation(
        confirmation_id, confirmed=True, resolved_by="user-1"
    )
    assert approved.decision.decision is GatewayDecisionType.ALLOW
    executed = await gateway.execute("request-1")
    assert executed.status == "EXECUTED"
    assert (tmp_path / "workspace/reports/a.md").read_text() == "hello"

    event_types = [item["type"] for item in await store.list_task_events("task-1")]
    assert "WAITING_CONFIRMATION" in event_types
    assert "CONFIRMED" in event_types
    assert "EXECUTION_STARTED" in event_types
    assert "EXECUTION_FINISHED" in event_types


@pytest.mark.asyncio
async def test_rejected_confirmation_never_executes(tmp_path: Path) -> None:
    contracts = ContractService()
    record = await _contract(contracts, confirm_required=True)
    gateway = ToolGateway(contracts=contracts, resolver=PermissionResolver(), executor=_executor(tmp_path))
    first = await gateway.evaluate(_call(record), _context())
    rejected = await gateway.resolve_confirmation(
        first.decision.confirmation_id or "", confirmed=False, resolved_by="user-1"
    )
    assert rejected.decision.decision is GatewayDecisionType.DENY
    with pytest.raises(GatewayError):
        await gateway.execute("request-1")
    assert not (tmp_path / "workspace/reports/a.md").exists()


@pytest.mark.asyncio
async def test_execute_rechecks_current_policy_and_contract(tmp_path: Path) -> None:
    contracts = ContractService()
    record = await _contract(contracts)
    gateway = ToolGateway(contracts=contracts, resolver=PermissionResolver(), executor=_executor(tmp_path))
    allowed = await gateway.evaluate(_call(record), _context())
    assert allowed.decision.decision is GatewayDecisionType.ALLOW

    await gateway.replace_permission_context("request-1", _context(system_effect=GrantEffect.DENY))
    with pytest.raises(GatewayError, match="SYSTEM_DENY"):
        await gateway.execute("request-1")
    assert not (tmp_path / "workspace/reports/a.md").exists()

    # A separate request referencing v1 is stale once v2 becomes confirmed.
    gateway2 = ToolGateway(contracts=contracts, resolver=PermissionResolver(), executor=_executor(tmp_path / "second"))
    await gateway2.evaluate(_call(record, "request-stale"), _context())
    updated = await contracts.update_contract(record.contract.contract_id, TaskContractUpdateRequest(goals=["new goal"]))
    await contracts.confirm_contract(record.contract.contract_id, version=updated.contract.version, confirmed_by="user-1")
    with pytest.raises(GatewayError, match="VERSION_STALE"):
        await gateway2.execute("request-stale")


@pytest.mark.asyncio
async def test_replan_creates_draft_and_blocks_old_request(tmp_path: Path) -> None:
    contracts = ContractService()
    record = await _contract(contracts)
    gateway = ToolGateway(contracts=contracts, resolver=PermissionResolver(), executor=_executor(tmp_path))
    await gateway.evaluate(_call(record), _context())
    draft = await gateway.replan("request-1", TaskContractUpdateRequest(goals=["replanned goal"]))
    assert draft.contract.version == 2
    assert draft.ref.status.value == "DRAFT"
    with pytest.raises(GatewayError, match="superseded by replan"):
        await gateway.execute("request-1")


@pytest.mark.asyncio
async def test_all_real_adapter_categories_reject_direct_bypass(tmp_path: Path) -> None:
    executor = _executor(tmp_path)
    contract_ref = ContractVersionRef(
        contract_id="c",
        version=1,
        digest="d",
        status=ContractVersionStatus.CONFIRMED,
    )

    def call(
        request_id: str,
        *,
        tool: str,
        action: str,
        canonical_args: dict[str, str],
        resource: str,
        effect_class: EffectClass,
    ) -> ToolCallEnvelope:
        return ToolCallEnvelope(
            request_id=request_id,
            task_id="task-1",
            session_id="session-1",
            contract_ref=contract_ref,
            skill_ref="x",
            tool=tool,
            action=action,
            canonical_args=canonical_args,
            resource=resource,
            effect_class=effect_class,
        )

    cases = [
        call(
            "r1",
            tool="create_file",
            action="create_file",
            canonical_args={"path": "a.txt", "content": "x"},
            resource="a.txt",
            effect_class=EffectClass.WRITE,
        ),
        call(
            "r2",
            tool="memory_write",
            action="memory_write",
            canonical_args={"key": "k", "value": "v"},
            resource="k",
            effect_class=EffectClass.MEMORY,
        ),
        call(
            "r3",
            tool="send_email_dry_run",
            action="send_email_dry_run",
            canonical_args={"to": "a@example.com", "subject": "s", "body": "b"},
            resource="a@example.com",
            effect_class=EffectClass.NETWORK,
        ),
        call(
            "r4",
            tool="run_shell",
            action="run_shell",
            canonical_args={"command": "ruff --version", "cwd": "."},
            resource=".",
            effect_class=EffectClass.PROCESS,
        ),
    ]
    for envelope in cases:
        with pytest.raises(AdapterBypassError):
            await executor.execute(envelope)
