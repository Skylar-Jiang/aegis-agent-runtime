from ra_agent.contracts import (
    ConfirmationPolicyV1,
    ContractPermissionRule,
    ContractVersionRef,
    ContractVersionStatus,
    EffectClass,
    GatewayReasonCode,
    GrantEffect,
    GrantScope,
    PermissionContext,
    PermissionGrant,
    TaskContractV2,
    ToolCallEnvelope,
)
from ra_agent.permissions import PermissionResolver


def _contract(*, resource: str = "reports/**") -> TaskContractV2:
    return TaskContractV2(
        contract_id="contract-1",
        session_id="session-1",
        task_id="task-1",
        user_id="user-1",
        version=1,
        goals=["write report"],
        allowed=[
            ContractPermissionRule(
                tool="create_file", action="create_file", resource=resource, effect="WRITE"
            )
        ],
        confirmation=ConfirmationPolicyV1(required_actions=[]),
        policy_version="policy:1",
        tool_manifest_digest="tools-v1",
    )


def _envelope(resource: str = "reports/a.md") -> ToolCallEnvelope:
    return ToolCallEnvelope(
        request_id="request-1",
        task_id="task-1",
        session_id="session-1",
        contract_ref=ContractVersionRef(
            contract_id="contract-1",
            version=1,
            digest="digest",
            status=ContractVersionStatus.CONFIRMED,
        ),
        skill_ref="writer",
        tool="create_file",
        action="create_file",
        canonical_args={"path": resource},
        resource=resource,
        effect_class=EffectClass.WRITE,
    )


def _grant(source: str, *, effect: GrantEffect = GrantEffect.ALLOW) -> PermissionGrant:
    return PermissionGrant(
        subject="user-1" if source == "user" else "*",
        skill="writer" if source == "skill" else "*",
        tool="create_file",
        action="create_file",
        resource="reports/**",
        effect=effect,
        scope=GrantScope.GLOBAL,
        source=source,
    )


def _context() -> PermissionContext:
    return PermissionContext(
        user_grants=[_grant("user")],
        skill_grants=[_grant("skill")],
        system_grants=[_grant("system")],
    )


def test_permission_intersection_all_layers_allow() -> None:
    result = PermissionResolver().resolve_effective_permission(
        _envelope(), _contract(), _context()
    )
    assert result.conflict_code is None
    assert not result.denied
    assert set(result.matched_sources) == {"user", "skill", "system"}


def test_permission_intersection_uses_smallest_numeric_limit() -> None:
    contract = _contract()
    contract.limits = {"max_affected_objects": 50, "mode": "strict"}
    context = _context()
    context.user_grants[0].limits = {"max_affected_objects": 100, "max_output_bytes": 4096}
    context.skill_grants[0].limits = {"max_affected_objects": 20, "max_output_bytes": 8192}
    context.system_grants[0].limits = {"max_affected_objects": 30, "max_output_bytes": 2048}

    result = PermissionResolver().resolve_effective_permission(
        _envelope(), contract, context
    )

    assert result.conflict_code is None
    assert result.constraints["max_affected_objects"] == 20
    assert result.constraints["max_output_bytes"] == 2048
    assert result.constraints["mode"] == "strict"


def test_system_deny_overrides_allow() -> None:
    context = _context()
    context.system_grants.append(_grant("system-deny", effect=GrantEffect.DENY))
    result = PermissionResolver().resolve_effective_permission(
        _envelope(), _contract(), context
    )
    assert result.conflict_code is GatewayReasonCode.SYSTEM_DENY
    assert result.denied


def test_resource_outside_contract_is_resource_mismatch() -> None:
    result = PermissionResolver().resolve_effective_permission(
        _envelope("private/a.md"), _contract(), _context()
    )
    assert result.conflict_code is GatewayReasonCode.RESOURCE_MISMATCH


def test_task_scope_must_bind_to_current_task() -> None:
    context = _context()
    context.user_grants = [
        PermissionGrant(
            subject="user-1",
            skill="*",
            tool="create_file",
            action="create_file",
            resource="reports/**",
            effect=GrantEffect.ALLOW,
            scope=GrantScope.TASK,
            scope_ref="another-task",
            source="user-task",
        )
    ]
    result = PermissionResolver().resolve_effective_permission(
        _envelope(), _contract(), context
    )
    assert result.conflict_code is GatewayReasonCode.USER_DENY
