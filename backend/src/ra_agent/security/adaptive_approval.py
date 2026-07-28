"""Deterministic evidence collection for context-aware human approval."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from ra_agent.contracts import (
    ApprovalRequest,
    DataLineage,
    PermissionCheckResult,
    PolicyDecision,
    RiskLevel,
    RiskVerdict,
    ToolCallRequest,
    ToolSpec,
)

from .rule_engine import RuleEngine


class AdaptiveApprovalEvaluator:
    """Shares adaptive approval evidence across Risk, Policy, and Permission.

    This class deliberately returns frozen Contract v0.4 objects or primitive
    signal names. It does not introduce a second approval model or decision
    enum.
    """

    _BLOCKING_RISKS = {RiskLevel.CRITICAL, RiskLevel.FORBIDDEN}

    def __init__(self, rules: RuleEngine) -> None:
        self.rules = rules

    def risk_signals(
        self,
        request: ToolCallRequest,
        tool_spec: ToolSpec,
    ) -> tuple[str, ...]:
        """Return deterministic escalation signals without inspecting effects."""

        if not self.rules.valid:
            return ("configuration_invalid",)

        signals: list[str] = []
        if (
            request.source_type in self.rules.adaptive_source_types
            and tool_spec.side_effect_type in self.rules.adaptive_side_effect_types
        ):
            signals.append("untrusted_data_flow")

        if request.tool_name == "send_email_dry_run":
            signals.extend(self._egress_signals(request))
        return tuple(dict.fromkeys(signals))

    def approval_reasons(
        self,
        request: ToolCallRequest,
        tool_spec: ToolSpec,
    ) -> tuple[str, ...]:
        """Explain only the evidence that requires a human decision."""

        reasons = [
            f"permission:{permission.value}"
            for permission in tool_spec.required_permissions
            if self.rules.permission_requires_approval(permission)
        ]
        reasons.extend(
            f"signal:{signal}"
            for signal in self.risk_signals(request, tool_spec)
            if self.rules.decision_for(self.rules.risk_for(signal))
            is PolicyDecision.REQUEST_APPROVAL
        )
        return tuple(dict.fromkeys(reasons))

    def build_request(
        self,
        request: ToolCallRequest,
        tool_spec: ToolSpec,
        verdict: RiskVerdict,
        permission: PermissionCheckResult,
        *,
        approval_id: str,
        request_fingerprint: str,
        requested_at: datetime | None = None,
        ttl: timedelta = timedelta(minutes=15),
    ) -> ApprovalRequest:
        """Build an explainable approval tied to the exact original request."""

        if not approval_id.strip():
            raise ValueError("approval_id must not be blank")
        if not request_fingerprint.strip():
            raise ValueError("request_fingerprint must not be blank")
        if verdict.request_id != request.request_id:
            raise ValueError("RiskVerdict.request_id does not match the original request")
        if tool_spec.name != request.tool_name:
            raise ValueError("ToolSpec.name does not match the original request")
        if permission.request_id != request.request_id or any(
            decision.request_id != request.request_id for decision in permission.decisions
        ):
            raise ValueError("Permission result does not match the original request")
        if verdict.risk_level in self._BLOCKING_RISKS:
            raise ValueError("blocked risk cannot be converted into an approval request")
        if (
            verdict.risk_level is not RiskLevel.HIGH
            and verdict.recommended_decision is not PolicyDecision.REQUEST_APPROVAL
            and not permission.requires_approval
        ):
            raise ValueError("request has no adaptive approval trigger")
        if ttl <= timedelta(0):
            raise ValueError("approval ttl must be positive")

        now = requested_at or datetime.now(UTC)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("requested_at must include a timezone")
        triggers = list(self.approval_reasons(request, tool_spec))
        if verdict.risk_level is RiskLevel.HIGH:
            triggers.append("risk:HIGH")
        triggers.extend(f"signal:{signal}" for signal in verdict.signals)
        evidence = ", ".join(dict.fromkeys(triggers)) or "policy:REQUEST_APPROVAL"
        return ApprovalRequest(
            approval_id=approval_id,
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            tool_name=request.tool_name,
            request_fingerprint=request_fingerprint,
            reason=(
                f"Adaptive approval required; risk={verdict.risk_level.value}; evidence={evidence}"
            ),
            requested_at=now,
            expires_at=now + ttl,
        )

    def _egress_signals(self, request: ToolCallRequest) -> list[str]:
        artifact = request.arguments.get("artifact")
        recipient = request.arguments.get("recipient")
        try:
            lineage = DataLineage.model_validate(artifact)
        except ValueError:
            return ["invalid_lineage"]

        signals: list[str] = []
        if lineage.sensitivity in self.rules.adaptive_lineage_sensitivities:
            signals.append("sensitive_egress")
        if (
            not isinstance(recipient, str)
            or not recipient.strip()
            or recipient not in lineage.allowed_recipients
        ):
            signals.append("unapproved_recipient")
        serialized = json.dumps(request.arguments, ensure_ascii=False, sort_keys=True, default=str)
        if self.rules.contains_secret(serialized):
            signals.append("secret_egress")
        return signals
