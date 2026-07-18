from typing import Protocol

from ra_agent.contracts import PolicyDecision, RiskLevel, RiskVerdict

from .rule_engine import RuleEngine


class PolicyEngine(Protocol):
    async def decide(self, verdict: RiskVerdict) -> PolicyDecision: ...


_DECISION_ORDER = {
    PolicyDecision.FAST_EXECUTE: 0,
    PolicyDecision.SANDBOX_CHECK: 1,
    PolicyDecision.REQUEST_APPROVAL: 2,
    PolicyDecision.BLOCK: 3,
}


class RuleBasedPolicyEngine:
    """Maps risk to the most restrictive applicable configured decision."""

    def __init__(self, rules: RuleEngine) -> None:
        self.rules = rules

    async def decide(self, verdict: RiskVerdict) -> PolicyDecision:
        if not self.rules.valid:
            return PolicyDecision.BLOCK
        configured = self.rules.decision_for(verdict.risk_level)
        minimum = self._minimum_decision(verdict.risk_level)
        decision = self._most_restrictive(configured, verdict.recommended_decision, minimum)
        if verdict.requires_checkpoint and decision is PolicyDecision.FAST_EXECUTE:
            return PolicyDecision.SANDBOX_CHECK
        return decision

    @staticmethod
    def _minimum_decision(risk: RiskLevel) -> PolicyDecision:
        if risk is RiskLevel.LOW:
            return PolicyDecision.FAST_EXECUTE
        if risk is RiskLevel.MEDIUM:
            return PolicyDecision.SANDBOX_CHECK
        if risk is RiskLevel.HIGH:
            return PolicyDecision.REQUEST_APPROVAL
        return PolicyDecision.BLOCK

    @staticmethod
    def _most_restrictive(*decisions: PolicyDecision) -> PolicyDecision:
        return max(decisions, key=_DECISION_ORDER.__getitem__)


class MockPolicyEngine:
    """Returns the classifier recommendation without enforcing real policy."""

    async def decide(self, verdict: RiskVerdict) -> PolicyDecision:
        return verdict.recommended_decision
