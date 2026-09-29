"""Small PR1 verification runner.

Run from repository root with the project's Python environment active:
    python scripts/verify_core_pr1.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from ra_agent.contracts import (  # noqa: E402
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
from ra_agent.gateway import FakeSignatureProvider, ToolGateway  # noqa: E402
from ra_agent.permissions import PermissionResolver  # noqa: E402


def grant(
    source: str,
    *,
    subject: str = "*",
    skill: str = "*",
    max_affected_objects: int,
) -> PermissionGrant:
    return PermissionGrant(
        subject=subject,
        skill=skill,
        tool="create_file",
        action="create_file",
        resource="reports/**",
        effect=GrantEffect.ALLOW,
        scope=GrantScope.GLOBAL,
        source=source,
        limits={"max_affected_objects": max_affected_objects},
    )


async def main() -> None:
    contracts = ContractService()
    first = await contracts.create_contract(
        TaskContractCreateRequest(
            session_id="session-pr1",
            task_id="task-pr1",
            user_id="user-pr1",
            goals=["Create reports/result.md"],
            allowed=[
                ContractPermissionRule(
                    tool="create_file",
                    action="create_file",
                    resource="reports/**",
                    effect="WRITE",
                )
            ],
            confirmation=ConfirmationPolicyV1(required_actions=["create_file"]),
            limits={"max_affected_objects": 50},
            policy_version="default:1",
            tool_manifest_digest="verify-tools-v1",
        )
    )
    confirmed = await contracts.confirm_contract(
        first.contract.contract_id, version=1, confirmed_by="user-pr1"
    )
    print("[PASS] TaskContractV2 create + confirm")

    permissions = PermissionContext(
        user_grants=[grant("user", subject="user-pr1", max_affected_objects=100)],
        skill_grants=[grant("skill", skill="report-writer", max_affected_objects=20)],
        system_grants=[grant("system", max_affected_objects=30)],
    )
    envelope = ToolCallEnvelope(
        request_id="request-pr1",
        task_id="task-pr1",
        session_id="session-pr1",
        contract_ref=confirmed.ref,
        skill_ref="report-writer",
        tool="create_file",
        action="create_file",
        canonical_args={"path": "reports/result.md", "content": "verified\n"},
        resource="reports/result.md",
        effect_class=EffectClass.WRITE,
    )
    gateway = ToolGateway(contracts=contracts, resolver=PermissionResolver())
    waiting = await gateway.evaluate(envelope, permissions)
    assert waiting.decision.decision is GatewayDecisionType.REQUIRE_CONFIRMATION
    assert waiting.decision.reason_code is GatewayReasonCode.CONFIRMATION_REQUIRED
    assert waiting.effective_permission.constraints["max_affected_objects"] == 20
    print("[PASS] permission intersection + minimum numeric limit + REQUIRE_CONFIRMATION")

    allowed = await gateway.resume_after_confirmation("request-pr1", confirmed=True)
    assert allowed.decision.decision is GatewayDecisionType.ALLOW
    executed = await gateway.execute("request-pr1")
    assert executed.status == "EXECUTED" and executed.result["dry_run"] is True
    print("[PASS] confirmation re-check + ALLOW + dry-run execute")

    second = await contracts.update_contract(
        first.contract.contract_id,
        TaskContractUpdateRequest(goals=["Create a revised report"]),
    )
    await contracts.confirm_contract(
        first.contract.contract_id, version=second.contract.version, confirmed_by="user-pr1"
    )
    stale = await gateway.evaluate(
        envelope.model_copy(update={"request_id": "request-stale"}), permissions
    )
    assert stale.decision.decision is GatewayDecisionType.REQUIRE_REPLAN
    assert stale.decision.reason_code is GatewayReasonCode.VERSION_STALE
    print("[PASS] stale contract version -> REQUIRE_REPLAN")

    signature = FakeSignatureProvider()
    payload = b"Aegis-Core-PR1"
    signed = signature.sign_sm2(payload, key_id="fake-core-key")
    assert signature.verify_sm2(payload, signed, key_id="fake-core-key")
    print("[PASS] FakeSignatureProvider contract (not real SM2)")

    events = await gateway.event_store.list_task_events("task-pr1")
    assert events and await gateway.event_store.get_chain_head("task-pr1")
    print(f"[PASS] InMemoryEventStore captured {len(events)} events")
    print("\nAegis Core P1 PR1 verification: ALL CHECKS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
