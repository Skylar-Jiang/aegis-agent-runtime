from datetime import UTC, datetime, timedelta

import pytest

from ra_agent.contracts import (
    ConfirmationPolicyV1,
    ContractPermissionRule,
    ContractService,
    EffectClass,
    GatewayDecisionType,
    GatewayReasonCode,
    GrantEffect,
    GrantScope,
    PermissionContext,
    PermissionGrant,
    TaskContractCreateRequest,
    TaskContractUpdateRequest,
    ToolCallEnvelope,
)
from ra_agent.gateway import GatewayError, ToolGateway
from ra_agent.permissions import PermissionResolver


async def _confirmed_contract(service: ContractService, *, confirmation: bool = False):
    record = await service.create_contract(
        TaskContractCreateRequest(
            session_id="session-1",
            task_id="task-1",
            user_id="user-1",
            goals=["write report"],
            allowed=[
                ContractPermissionRule(
                    tool="create_file",
                    action="create_file",
                    resource="reports/**",
                    effect="WRITE",
                )
            ],
            confirmation=ConfirmationPolicyV1(
                required_actions=["create_file"] if confirmation else []
            ),
            policy_version="policy:1",
            tool_manifest_digest="tools-v1",
        )
    )
    return await service.confirm_contract(
        record.contract.contract_id, version=1, confirmed_by="user-1"
    )


def _context() -> PermissionContext:
    def grant(source: str, subject: str = "*", skill: str = "*") -> PermissionGrant:
        return PermissionGrant(
            subject=subject,
            skill=skill,
            tool="create_file",
            action="create_file",
            resource="reports/**",
            effect=GrantEffect.ALLOW,
            scope=GrantScope.GLOBAL,
            source=source,
        )

    return PermissionContext(
        user_grants=[grant("user", subject="user-1")],
        skill_grants=[grant("skill", skill="writer")],
        system_grants=[grant("system")],
    )


def _envelope(record, request_id: str = "request-1") -> ToolCallEnvelope:
    return ToolCallEnvelope(
        request_id=request_id,
        task_id=record.contract.task_id,
        session_id=record.contract.session_id,
        contract_ref=record.ref,
        skill_ref="writer",
        tool="create_file",
        action="create_file",
        canonical_args={"path": "reports/a.md", "content": "hello"},
        resource="reports/a.md",
        effect_class=EffectClass.WRITE,
    )


@pytest.mark.asyncio
async def test_gateway_allow_then_execute_emits_pr2_lifecycle() -> None:
    contracts = ContractService()
    record = await _confirmed_contract(contracts)
    gateway = ToolGateway(contracts=contracts, resolver=PermissionResolver())
    evaluated = await gateway.evaluate(_envelope(record), _context())
    assert evaluated.decision.decision is GatewayDecisionType.ALLOW
    assert evaluated.decision.reason_code is GatewayReasonCode.ALLOWED

    executed = await gateway.execute("request-1")
    assert executed.status == "EXECUTED"
    assert executed.result["dry_run"] is True
    events = await gateway.event_store.list_task_events("task-1")
    assert [item["type"] for item in events] == [
        "PERMISSION_EVALUATED",
        "GATEWAY_ALLOWED",
        "PERMISSION_EVALUATED",
        "GATEWAY_ALLOWED",
        "EXECUTION_STARTED",
        "EXECUTION_FINISHED",
    ]


@pytest.mark.asyncio
async def test_gateway_execute_rechecks_expired_permission_before_side_effect() -> None:
    contracts = ContractService()
    record = await _confirmed_contract(contracts)
    context = _context()
    gateway = ToolGateway(contracts=contracts, resolver=PermissionResolver())
    evaluated = await gateway.evaluate(_envelope(record), context)
    assert evaluated.decision.decision is GatewayDecisionType.ALLOW

    context.user_grants[0].expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await gateway.replace_permission_context("request-1", context)

    with pytest.raises(GatewayError, match="USER_DENY"):
        await gateway.execute("request-1")
    events = await gateway.event_store.list_task_events("task-1")
    assert "EXECUTION_FINISHED" not in [item["type"] for item in events]


@pytest.mark.asyncio
async def test_gateway_confirmation_basic_rechecks_before_allow() -> None:
    contracts = ContractService()
    record = await _confirmed_contract(contracts, confirmation=True)
    gateway = ToolGateway(contracts=contracts, resolver=PermissionResolver())
    first = await gateway.evaluate(_envelope(record), _context())
    assert first.decision.decision is GatewayDecisionType.REQUIRE_CONFIRMATION
    assert first.decision.confirmation_id

    resumed = await gateway.resume_after_confirmation("request-1", confirmed=True)
    assert resumed.decision.decision is GatewayDecisionType.ALLOW


@pytest.mark.asyncio
async def test_gateway_requires_replan_when_contract_version_is_stale() -> None:
    contracts = ContractService()
    first = await _confirmed_contract(contracts)
    second = await contracts.update_contract(
        first.contract.contract_id,
        TaskContractUpdateRequest(goals=["write revised report"]),
    )
    await contracts.confirm_contract(
        first.contract.contract_id, version=second.contract.version, confirmed_by="user-1"
    )
    gateway = ToolGateway(contracts=contracts, resolver=PermissionResolver())
    result = await gateway.evaluate(_envelope(first), _context())
    assert result.decision.decision is GatewayDecisionType.REQUIRE_REPLAN
    assert result.decision.reason_code is GatewayReasonCode.VERSION_STALE
