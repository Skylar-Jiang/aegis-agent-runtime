from typing import Protocol

from ra_agent.contracts import PolicyDecision, RiskLevel, RiskVerdict, ToolCallRequest


class RiskClassifier(Protocol):
    async def classify(self, request: ToolCallRequest) -> RiskVerdict: ...


class MockRiskClassifier:
    """Deterministic Phase 0 mock; this is not a real security control."""

    async def classify(self, request: ToolCallRequest) -> RiskVerdict:
        return RiskVerdict(
            request_id=request.request_id,
            risk_level=RiskLevel.LOW,
            recommended_decision=PolicyDecision.FAST_EXECUTE,
            reason="Phase 0 deterministic mock verdict",
        )
