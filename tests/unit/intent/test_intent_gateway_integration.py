from __future__ import annotations

import asyncio
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
    ToolCallEnvelope,
)
from ra_agent.core.ids import new_id
from ra_agent.gateway import ToolGateway
from ra_agent.intent import (
    BaselineIntentDetector,
    DecisionResult,
    IntentDecisionType,
    IntentEnforcer,
    IntentRegistry,
    IntentSpec,
)
from ra_agent.permissions import PermissionResolver


async def _core_contract():
    service = ContractService()
    record = await service.create_contract(
        TaskContractCreateRequest(
            session_id="session-1",
            task_id="task-1",
            user_id="user-1",
            goals=["prepare report"],
            allowed=[
                ContractPermissionRule(
                    tool="create_file",
                    action="create_file",
                    resource="reports/**",
                    effect="WRITE",
                )
            ],
            confirmation=ConfirmationPolicyV1(),
            policy_version="policy:1",
            tool_manifest_digest="tools-v1",
        )
    )
    confirmed = await service.confirm_contract(
        record.contract.contract_id,
        version=1,
        confirmed_by="user-1",
    )
    return service, confirmed


def _permissions() -> PermissionContext:
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


def _envelope(record) -> ToolCallEnvelope:
    return ToolCallEnvelope(
        request_id="request-1",
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


@pytest.mark.asyncio
async def test_intent_decision_blocks_core_allowed_action_before_execution() -> None:
    contracts, record = await _core_contract()
    registry = IntentRegistry()
    spec = IntentSpec(
        intent_id=new_id("intent"),
        task_id="task-1",
        goal="Read source material only",
        scope=["reports/**"],
        allowed_actions=["read"],
        forbidden_actions=["write"],
        success_criteria=["source inspected"],
        source_refs=["user-request:1"],
        version=1,
    )
    spec, _ = await registry.create(spec)
    await registry.confirm(spec.intent_id, confirmed_by="user-1")

    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        intent_enforcer=IntentEnforcer(BaselineIntentDetector(registry)),
    )
    result = await gateway.evaluate(_envelope(record), _permissions())
    assert result.decision.decision is GatewayDecisionType.DENY
    assert result.decision.reason_code is GatewayReasonCode.INTENT_DRIFT
    assert result.decision.versions["intent_detector"] == "intent-baseline-boundary-v1"


@pytest.mark.asyncio
async def test_unbound_task_preserves_core_behavior() -> None:
    contracts, record = await _core_contract()
    registry = IntentRegistry()
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        intent_enforcer=IntentEnforcer(BaselineIntentDetector(registry)),
    )
    result = await gateway.evaluate(_envelope(record), _permissions())
    assert result.decision.decision is GatewayDecisionType.ALLOW


class _SlowDetector:
    async def evaluate(self, _context):
        await asyncio.sleep(0.05)
        return DecisionResult(
            decision_id="late",
            decision=IntentDecisionType.CONTINUE,
            risk_score=0.0,
            trigger_dimensions=[],
            evidence_refs=[],
            reason_code="LATE",
            policy_version="p",
            detector_version="d",
            expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )


@pytest.mark.asyncio
async def test_detector_timeout_fails_closed() -> None:
    contracts, record = await _core_contract()
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        intent_enforcer=IntentEnforcer(_SlowDetector(), timeout_seconds=0.001),
    )
    result = await gateway.evaluate(_envelope(record), _permissions())
    assert result.decision.decision is GatewayDecisionType.DENY
    assert result.decision.reason_code is GatewayReasonCode.INTENT_CHECK_TIMEOUT

class _FlipDetector:
    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, _context):
        self.calls += 1
        decision = IntentDecisionType.CONTINUE if self.calls == 1 else IntentDecisionType.BLOCK
        return DecisionResult(
            decision_id=f"flip-{self.calls}",
            decision=decision,
            risk_score=0.0 if decision is IntentDecisionType.CONTINUE else 1.0,
            trigger_dimensions=[] if decision is IntentDecisionType.CONTINUE else ["sequence"],
            evidence_refs=[],
            reason_code="OK" if decision is IntentDecisionType.CONTINUE else "DRIFT",
            policy_version="p",
            detector_version="flip-v1",
            expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )


@pytest.mark.asyncio
async def test_execute_rechecks_intent_before_side_effect() -> None:
    from ra_agent.gateway import GatewayError

    contracts, record = await _core_contract()
    detector = _FlipDetector()
    gateway = ToolGateway(
        contracts=contracts,
        resolver=PermissionResolver(),
        intent_enforcer=IntentEnforcer(detector),
    )
    first = await gateway.evaluate(_envelope(record), _permissions())
    assert first.decision.decision is GatewayDecisionType.ALLOW

    with pytest.raises(GatewayError, match="Pre-execution recheck failed"):
        await gateway.execute("request-1")
    events = await gateway.event_store.list_task_events("task-1")
    assert "EXECUTION_FINISHED" not in [event["type"] for event in events]
