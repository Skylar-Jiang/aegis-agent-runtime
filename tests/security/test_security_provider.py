from collections.abc import Callable
from pathlib import Path

import pytest
from ra_agent.contracts import (
    PermissionStatus,
    PolicyDecision,
    RiskLevel,
    ToolCallRequest,
)
from ra_agent.core.bootstrap import build_mock_container
from ra_agent.security.permission_gate import RuleBasedPermissionGate
from ra_agent.security.policy_engine import RuleBasedPolicyEngine
from ra_agent.security.risk_classifier import RuleBasedRiskClassifier
from ra_agent.security.security_provider import (
    RiskPolicySecurityProvider,
    build_security_provider,
)

ROOT = Path(__file__).resolve().parents[2]


def test_security_provider_installs_only_member_owned_dependencies() -> None:
    original = build_mock_container()
    provider = build_security_provider(ROOT / "configs")

    installed = provider.install(original)

    assert provider.name == "v2-risk-policy"
    assert isinstance(installed.risk_classifier, RuleBasedRiskClassifier)
    assert isinstance(installed.policy_engine, RuleBasedPolicyEngine)
    assert isinstance(installed.permission_gate, RuleBasedPermissionGate)
    assert installed.tool_executor is original.tool_executor
    assert installed.approval_service is original.approval_service
    assert installed.tool_registry is original.tool_registry


@pytest.mark.asyncio
async def test_security_provider_fails_closed_when_config_is_missing(
    tmp_path: Path,
    request_factory: Callable[..., ToolCallRequest],
) -> None:
    original = build_mock_container()
    installed = RiskPolicySecurityProvider(tmp_path / "missing").install(original)
    request: ToolCallRequest = request_factory("list_dir", arguments={"path": "."})
    spec = installed.tool_registry.get_spec("list_dir")

    verdict = await installed.risk_classifier.classify(request)
    decision = await installed.policy_engine.decide(verdict)
    permission = await installed.permission_gate.check(request, spec)

    assert verdict.risk_level is RiskLevel.FORBIDDEN
    assert decision is PolicyDecision.BLOCK
    assert not permission.allowed
    assert permission.decisions[0].status is PermissionStatus.DENIED
