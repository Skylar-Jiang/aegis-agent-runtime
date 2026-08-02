from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from ra_agent.contracts import (
    ApprovalStatus,
    PermissionCheckResult,
    PolicyDecision,
    RiskLevel,
    RiskVerdict,
    SourceType,
    ToolCallRequest,
    ToolSpec,
)
from ra_agent.security.adaptive_approval import AdaptiveApprovalEvaluator
from ra_agent.security.approval_service import MockApprovalService
from ra_agent.security.permission_gate import RuleBasedPermissionGate
from ra_agent.security.risk_classifier import RuleBasedRiskClassifier
from ra_agent.security.rule_engine import RuleEngine


async def _approval_evidence(
    rules: RuleEngine,
    tool_specs: dict[str, ToolSpec],
    request: ToolCallRequest,
) -> tuple[RiskVerdict, PermissionCheckResult]:
    evaluator = AdaptiveApprovalEvaluator(rules)
    verdict = await RuleBasedRiskClassifier(
        rules,
        tool_specs,
        approval_evaluator=evaluator,
    ).classify(request)
    permission = await RuleBasedPermissionGate(
        rules,
        approval_evaluator=evaluator,
    ).check(request, tool_specs[request.tool_name])
    return verdict, permission


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("grant", ApprovalStatus.GRANTED),
        ("deny", ApprovalStatus.DENIED),
        ("expire", ApprovalStatus.EXPIRED),
    ],
)
async def test_adaptive_approval_grant_deny_and_expire_are_correlated(
    rules: RuleEngine,
    tool_specs: dict[str, ToolSpec],
    request_factory: Callable[..., ToolCallRequest],
    action: str,
    expected: ApprovalStatus,
) -> None:
    request = request_factory(
        "write_file",
        arguments={"path": "reports/result.md", "content": "external summary"},
        source_type=SourceType.EXTERNAL_DOCUMENT,
        request_id=f"request-{action}",
    )
    verdict, permission = await _approval_evidence(rules, tool_specs, request)
    evaluator = AdaptiveApprovalEvaluator(rules)
    requested_at = datetime.now(UTC)
    approval = evaluator.build_request(
        request,
        tool_specs["write_file"],
        verdict,
        permission,
        approval_id=f"approval-{action}",
        request_fingerprint=f"fingerprint-{action}",
        requested_at=requested_at,
        ttl=timedelta(minutes=5),
    )
    service = MockApprovalService()
    await service.create(approval)

    if action == "grant":
        decision = await service.grant(
            approval.approval_id,
            "security-reviewer",
            "evidence reviewed",
            decided_at=requested_at + timedelta(seconds=1),
        )
    elif action == "deny":
        decision = await service.deny(
            approval.approval_id,
            "security-reviewer",
            "request rejected",
            decided_at=requested_at + timedelta(seconds=1),
        )
    else:
        decision = await service.expire(
            approval.approval_id,
            decided_at=requested_at + timedelta(minutes=5),
        )

    assert verdict.risk_level is RiskLevel.HIGH
    assert verdict.recommended_decision is PolicyDecision.REQUEST_APPROVAL
    assert approval.task_id == request.task_id
    assert approval.step_id == request.step_id
    assert approval.request_id == request.request_id
    assert approval.tool_name == request.tool_name
    assert approval.expires_at == requested_at + timedelta(minutes=5)
    assert "signal:untrusted_data_flow" in approval.reason
    assert decision.status is expected
    assert decision.request_id == request.request_id


@pytest.mark.asyncio
async def test_adaptive_approval_rejects_mismatched_original_request(
    rules: RuleEngine,
    tool_specs: dict[str, ToolSpec],
    request_factory: Callable[..., ToolCallRequest],
) -> None:
    request = request_factory(
        "delete_file",
        arguments={"path": "old.txt"},
        request_id="request-original",
    )
    verdict, permission = await _approval_evidence(rules, tool_specs, request)
    mismatched = verdict.model_copy(update={"request_id": "request-other"})

    with pytest.raises(ValueError, match="original request"):
        AdaptiveApprovalEvaluator(rules).build_request(
            request,
            tool_specs["delete_file"],
            mismatched,
            permission,
            approval_id="approval-mismatch",
            request_fingerprint="fingerprint-mismatch",
        )


def test_blocking_risk_cannot_be_relabelled_as_human_approval(
    rules: RuleEngine,
    tool_specs: dict[str, ToolSpec],
    request_factory: Callable[..., ToolCallRequest],
) -> None:
    request = request_factory("run_shell", arguments={"command": "rm -rf ./data"})
    verdict = RiskVerdict(
        request_id=request.request_id,
        risk_level=RiskLevel.CRITICAL,
        recommended_decision=PolicyDecision.BLOCK,
        reason="dangerous shell",
    )
    permission = PermissionCheckResult(
        request_id=request.request_id,
        decisions=[],
        allowed=False,
        requires_approval=False,
        reason="blocked",
    )

    with pytest.raises(ValueError, match="blocked risk"):
        AdaptiveApprovalEvaluator(rules).build_request(
            request,
            tool_specs["run_shell"],
            verdict,
            permission,
            approval_id="approval-critical",
            request_fingerprint="fingerprint-critical",
        )
