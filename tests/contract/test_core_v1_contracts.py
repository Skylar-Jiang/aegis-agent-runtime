from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ra_agent.contracts import (
    ContractPermissionRule,
    ContractVersionRef,
    ContractVersionStatus,
    EffectClass,
    GatewayDecision,
    GatewayDecisionType,
    GatewayReasonCode,
    GrantEffect,
    GrantScope,
    PermissionGrant,
    TaskContractV2,
    ToolCallEnvelope,
)


def test_core_v1_public_field_names_are_frozen() -> None:
    assert set(TaskContractV2.model_fields) >= {
        "contract_id",
        "session_id",
        "task_id",
        "user_id",
        "version",
        "goals",
        "completion_criteria",
        "allowed",
        "denied",
        "limits",
        "confirmation",
        "policy_version",
        "tool_manifest_digest",
        "parent_digest",
    }
    assert set(ToolCallEnvelope.model_fields) == {
        "request_id",
        "task_id",
        "session_id",
        "contract_ref",
        "skill_ref",
        "tool",
        "action",
        "canonical_args",
        "resource",
        "effect_class",
    }
    assert set(GatewayDecision.model_fields) == {
        "decision",
        "reason_code",
        "evidence_refs",
        "versions",
        "confirmation_id",
    }


def test_non_global_permission_grant_requires_scope_ref() -> None:
    with pytest.raises(ValidationError):
        PermissionGrant(
            subject="user-1",
            skill="writer",
            tool="create_file",
            action="create_file",
            resource="reports/**",
            effect=GrantEffect.ALLOW,
            scope=GrantScope.TASK,
            source="user",
        )


def test_permission_grant_rejects_invalid_numeric_limits() -> None:
    with pytest.raises(ValidationError):
        PermissionGrant(
            subject="user-1",
            skill="writer",
            tool="create_file",
            action="create_file",
            resource="reports/**",
            effect=GrantEffect.ALLOW,
            scope=GrantScope.GLOBAL,
            source="user",
            limits={"max_affected_objects": -1},
        )


def test_core_models_round_trip_json() -> None:
    contract = TaskContractV2(
        contract_id="contract-1",
        session_id="session-1",
        task_id="task-1",
        user_id="user-1",
        version=1,
        goals=["create report"],
        allowed=[ContractPermissionRule(action="create_file", resource="reports/**")],
        policy_version="policy:1",
        tool_manifest_digest="abc",
    )
    ref = ContractVersionRef(
        contract_id=contract.contract_id,
        version=1,
        digest="digest",
        status=ContractVersionStatus.CONFIRMED,
        confirmed_by="tester",
        confirmed_at=datetime.now(UTC),
    )
    envelope = ToolCallEnvelope(
        request_id="request-1",
        task_id=contract.task_id,
        session_id=contract.session_id,
        contract_ref=ref,
        skill_ref="writer",
        tool="create_file",
        action="create_file",
        canonical_args={"path": "reports/a.md"},
        resource="reports/a.md",
        effect_class=EffectClass.WRITE,
    )
    decision = GatewayDecision(
        decision=GatewayDecisionType.ALLOW,
        reason_code=GatewayReasonCode.ALLOWED,
        evidence_refs=[ref.digest],
        versions={"contract": 1},
    )
    assert ToolCallEnvelope.model_validate_json(envelope.model_dump_json()) == envelope
    assert GatewayDecision.model_validate_json(decision.model_dump_json()) == decision
