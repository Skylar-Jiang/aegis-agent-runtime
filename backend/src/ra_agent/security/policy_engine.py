from typing import Protocol

from ra_agent.contracts import PolicyDecision, RiskVerdict


class PolicyEngine(Protocol):
    async def decide(self, verdict: RiskVerdict) -> PolicyDecision: ...


class MockPolicyEngine:
    """Returns the classifier recommendation without enforcing real policy."""

    async def decide(self, verdict: RiskVerdict) -> PolicyDecision:
        return verdict.recommended_decision
