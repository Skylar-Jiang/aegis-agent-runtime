from typing import Protocol

from ra_agent.contracts import PolicyDecision, RiskLevel, RiskVerdict, ToolCallRequest


class RiskClassifier(Protocol):
    async def classify(self, request: ToolCallRequest) -> RiskVerdict: ...


class MockRiskClassifier:
    """Deterministic fixture baseline; this is not a real security control."""

    async def classify(self, request: ToolCallRequest) -> RiskVerdict:
        command = str(request.arguments.get("command", "")).lower()
        path = str(request.arguments.get("path", "")).lower()

        if request.tool_name == "run_shell" and "rm -rf" in command:
            return self._verdict(
                request,
                RiskLevel.CRITICAL,
                PolicyDecision.BLOCK,
                "Mock baseline blocks destructive shell commands",
                signals=["dangerous_shell"],
                matched_rules=["fixture.dangerous_shell"],
            )
        if request.tool_name == "read_file" and path == ".env":
            return self._verdict(
                request,
                RiskLevel.HIGH,
                PolicyDecision.REQUEST_APPROVAL,
                "Mock baseline requires approval for sensitive reads",
                signals=["sensitive_read"],
                matched_rules=["fixture.sensitive_read"],
                requires_deep_check=True,
                requires_checkpoint=True,
            )
        if request.tool_name in {"write_file", "download_url", "memory_write"}:
            return self._verdict(
                request,
                RiskLevel.MEDIUM,
                PolicyDecision.SANDBOX_CHECK,
                "Mock baseline routes writes through sandbox policy",
                requires_deep_check=True,
                requires_checkpoint=True,
            )
        if request.tool_name in {"delete_file", "run_shell"}:
            return self._verdict(
                request,
                RiskLevel.HIGH,
                PolicyDecision.REQUEST_APPROVAL,
                "Mock baseline requires approval for high-risk tools",
                requires_deep_check=True,
                requires_checkpoint=True,
            )
        if request.tool_name in {"list_dir", "read_file", "memory_read"}:
            return self._verdict(
                request,
                RiskLevel.LOW,
                PolicyDecision.FAST_EXECUTE,
                "Mock baseline permits known read-only tools",
            )
        return self._verdict(
            request,
            RiskLevel.HIGH,
            PolicyDecision.REQUEST_APPROVAL,
            "Mock baseline uses a conservative default for unknown semantics",
            requires_deep_check=True,
        )

    @staticmethod
    def _verdict(
        request: ToolCallRequest,
        risk_level: RiskLevel,
        decision: PolicyDecision,
        reason: str,
        *,
        signals: list[str] | None = None,
        matched_rules: list[str] | None = None,
        requires_deep_check: bool = False,
        requires_checkpoint: bool = False,
    ) -> RiskVerdict:
        return RiskVerdict(
            request_id=request.request_id,
            risk_level=risk_level,
            recommended_decision=decision,
            reason=reason,
            signals=signals or [],
            matched_rules=matched_rules or [],
            requires_deep_check=requires_deep_check,
            requires_checkpoint=requires_checkpoint,
        )
