"""Member-owned V2 security dependency provider.

The group lead may register this provider from bootstrap. This module itself
does not mutate the public container, router, or Runtime wiring.
"""

from dataclasses import replace
from pathlib import Path

from ra_agent.core.container import ServiceContainer
from ra_agent.core.providers import SecurityProvider

from .adaptive_approval import AdaptiveApprovalEvaluator
from .permission_gate import RuleBasedPermissionGate
from .policy_engine import RuleBasedPolicyEngine
from .risk_classifier import RuleBasedRiskClassifier
from .rule_engine import RuleEngine


class RiskPolicySecurityProvider:
    """Install one consistent RuleEngine across Risk, Policy, and Permission."""

    name = "v2-risk-policy"

    def __init__(self, config_directory: Path) -> None:
        self.rules = RuleEngine.from_directory(config_directory)
        self.approval_evaluator = AdaptiveApprovalEvaluator(self.rules)

    def install(self, container: ServiceContainer) -> ServiceContainer:
        tool_specs = {spec.name: spec for spec in container.tool_registry.list_specs()}
        return replace(
            container,
            risk_classifier=RuleBasedRiskClassifier(
                self.rules,
                tool_specs,
                approval_evaluator=self.approval_evaluator,
            ),
            policy_engine=RuleBasedPolicyEngine(self.rules),
            permission_gate=RuleBasedPermissionGate(
                self.rules,
                approval_evaluator=self.approval_evaluator,
            ),
        )


def build_security_provider(config_directory: Path) -> SecurityProvider:
    """Return the frozen provider Protocol instead of a private interface."""

    return RiskPolicySecurityProvider(config_directory)
