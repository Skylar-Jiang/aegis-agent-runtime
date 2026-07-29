from collections.abc import Callable

import pytest

from ra_agent.contracts import (
    PermissionStatus,
    PermissionType,
    PolicyDecision,
    RecoverabilityType,
    RiskLevel,
    RiskVerdict,
    SourceType,
    ToolCallRequest,
    ToolSpec,
)
from ra_agent.security.permission_gate import RuleBasedPermissionGate
from ra_agent.security.policy_engine import RuleBasedPolicyEngine
from ra_agent.security.rule_engine import RuleEngine


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("risk", "expected"),
    [
        (RiskLevel.LOW, PolicyDecision.FAST_EXECUTE),
        (RiskLevel.MEDIUM, PolicyDecision.SANDBOX_CHECK),
        (RiskLevel.HIGH, PolicyDecision.REQUEST_APPROVAL),
        (RiskLevel.CRITICAL, PolicyDecision.BLOCK),
        (RiskLevel.FORBIDDEN, PolicyDecision.BLOCK),
    ],
)
async def test_policy_mapping(
    rules: RuleEngine, risk: RiskLevel, expected: PolicyDecision
) -> None:
    verdict = RiskVerdict(
        request_id="request-policy",
        risk_level=risk,
        recommended_decision=PolicyDecision.FAST_EXECUTE,
        reason="test",
    )

    assert await RuleBasedPolicyEngine(rules).decide(verdict) is expected


@pytest.mark.asyncio
async def test_invalid_policy_configuration_blocks() -> None:
    verdict = RiskVerdict(
        request_id="request-policy",
        risk_level=RiskLevel.LOW,
        recommended_decision=PolicyDecision.FAST_EXECUTE,
        reason="test",
    )

    assert (
        await RuleBasedPolicyEngine(RuleEngine.invalid("broken")).decide(verdict)
        is PolicyDecision.BLOCK
    )


@pytest.mark.asyncio
async def test_known_permission_is_granted(
    rules: RuleEngine,
    tool_specs: dict[str, ToolSpec],
    request_factory: Callable[..., ToolCallRequest],
) -> None:
    request = request_factory("list_dir", arguments={"path": "."})
    result = await RuleBasedPermissionGate(rules).check(request, tool_specs["list_dir"])

    assert result.request_id == request.request_id
    assert result.allowed
    assert not result.requires_approval
    assert result.decisions[0].status is PermissionStatus.GRANTED
    assert result.decisions[0].request_id == request.request_id


@pytest.mark.asyncio
async def test_delete_permission_requires_runtime_approval(
    rules: RuleEngine,
    tool_specs: dict[str, ToolSpec],
    request_factory: Callable[..., ToolCallRequest],
) -> None:
    request = request_factory("delete_file", arguments={"path": "draft.txt"})
    result = await RuleBasedPermissionGate(rules).check(
        request, tool_specs["delete_file"]
    )

    assert result.allowed
    assert result.requires_approval
    assert result.decisions[0].status is PermissionStatus.GRANTED


@pytest.mark.asyncio
async def test_untrusted_write_permission_requires_adaptive_approval(
    rules: RuleEngine,
    tool_specs: dict[str, ToolSpec],
    request_factory: Callable[..., ToolCallRequest],
) -> None:
    request = request_factory(
        "write_file",
        arguments={"path": "reports/result.md", "content": "derived"},
        source_type=SourceType.EXTERNAL_DOCUMENT,
    )

    result = await RuleBasedPermissionGate(rules).check(
        request, tool_specs["write_file"]
    )

    assert result.allowed
    assert result.requires_approval
    assert "signal:untrusted_data_flow" in result.reason


@pytest.mark.asyncio
async def test_user_write_permission_does_not_add_manual_action(
    rules: RuleEngine,
    tool_specs: dict[str, ToolSpec],
    request_factory: Callable[..., ToolCallRequest],
) -> None:
    request = request_factory(
        "write_file",
        arguments={"path": "reports/result.md", "content": "user-authored"},
    )

    result = await RuleBasedPermissionGate(rules).check(
        request, tool_specs["write_file"]
    )

    assert result.allowed
    assert not result.requires_approval
    assert result.reason == "All required permissions are granted"


@pytest.mark.asyncio
async def test_permission_order_does_not_change_authorization(
    rules: RuleEngine,
    tool_specs: dict[str, ToolSpec],
    request_factory: Callable[..., ToolCallRequest],
) -> None:
    request = request_factory(
        "download_url",
        arguments={"url": "https://example.com/file", "destination": "file"},
    )
    reordered = tool_specs["download_url"].model_copy(
        update={
            "required_permissions": [
                PermissionType.FILE_WRITE,
                PermissionType.NETWORK_DOWNLOAD,
            ]
        }
    )

    result = await RuleBasedPermissionGate(rules).check(request, reordered)

    assert result.allowed
    assert {item.permission for item in result.decisions} == {
        PermissionType.FILE_WRITE,
        PermissionType.NETWORK_DOWNLOAD,
    }


@pytest.mark.asyncio
async def test_unknown_permission_is_denied(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
) -> None:
    request = request_factory("custom_tool")
    unknown_spec = ToolSpec(
        name="custom_tool",
        description="test unknown permission",
        required_permissions=[PermissionType.SYSTEM_MODIFY],
        base_risk=RiskLevel.HIGH,
        side_effect_type="WRITE",
        reversibility=RecoverabilityType.NON_REVERSIBLE,
        sandbox_mode="DISABLED",
        timeout_seconds=1,
        network_required=False,
        supports_dry_run=False,
    )

    result = await RuleBasedPermissionGate(rules).check(request, unknown_spec)

    assert not result.allowed
    assert result.decisions[0].status is PermissionStatus.DENIED
    assert result.request_id == request.request_id


@pytest.mark.asyncio
async def test_invalid_permission_configuration_denies(
    tool_specs: dict[str, ToolSpec],
    request_factory: Callable[..., ToolCallRequest],
) -> None:
    request = request_factory("list_dir")
    result = await RuleBasedPermissionGate(RuleEngine.invalid("broken")).check(
        request, tool_specs["list_dir"]
    )

    assert not result.allowed
    assert result.decisions[0].status is PermissionStatus.DENIED
