from datetime import UTC, datetime, timedelta

import pytest

from ra_agent.contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    CheckpointResult,
    CommitResult,
    DeepCheckResult,
    ExecutionStatus,
    PolicyDecision,
    RiskLevel,
    RiskVerdict,
    RollbackResult,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.runtime.correlation import (
    CorrelationError,
    validate_approval_decision,
    validate_approval_request,
    validate_checkpoint,
    validate_commit,
    validate_deep_check,
    validate_execution,
    validate_risk,
    validate_rollback,
)


def models():
    now = datetime.now(UTC)
    request = ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id="request-1",
        tool_name="write_file",
        arguments={"path": "draft.txt"},
        objective="update draft",
        context_summary="pending write",
        source_type=SourceType.USER,
        requested_at=now,
    )
    checkpoint = CheckpointResult(
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        checkpoint_id="checkpoint-1",
        status=ExecutionStatus.SUCCESS,
    )
    execution = ToolExecutionResult(
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        checkpoint_id=checkpoint.checkpoint_id,
        status=ExecutionStatus.PENDING_COMMIT,
    )
    approval = ApprovalRequest(
        approval_id="approval-1",
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        tool_name=request.tool_name,
        request_fingerprint="fingerprint-1",
        reason="review",
        requested_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    decision = ApprovalDecision(
        approval_id=approval.approval_id,
        task_id=approval.task_id,
        step_id=approval.step_id,
        request_id=approval.request_id,
        status=ApprovalStatus.GRANTED,
        decided_by="reviewer",
        decided_at=now,
        reason="approved",
    )
    return request, checkpoint, execution, approval, decision


def test_all_valid_cross_module_results_are_accepted() -> None:
    request, checkpoint, execution, approval, decision = models()
    validate_risk(
        request,
        RiskVerdict(
            request_id=request.request_id,
            risk_level=RiskLevel.MEDIUM,
            recommended_decision=PolicyDecision.SANDBOX_CHECK,
            reason="mock",
        ),
    )
    validate_checkpoint(request, checkpoint)
    validate_execution(request, execution, checkpoint_id=checkpoint.checkpoint_id)
    validate_deep_check(
        request,
        DeepCheckResult(request_id=request.request_id, passed=True, reason="mock"),
    )
    validate_commit(
        request,
        checkpoint.checkpoint_id,
        CommitResult(
            request_id=request.request_id,
            checkpoint_id=checkpoint.checkpoint_id,
            status=ExecutionStatus.COMMITTED,
        ),
    )
    validate_rollback(
        request,
        checkpoint.checkpoint_id,
        RollbackResult(
            request_id=request.request_id,
            checkpoint_id=checkpoint.checkpoint_id,
            status=ExecutionStatus.ROLLED_BACK,
            reason="mock",
        ),
    )
    validate_approval_request(request, approval.request_fingerprint, approval)
    validate_approval_decision(approval, decision)


@pytest.mark.parametrize(
    ("label", "validate"),
    [
        (
            "RiskVerdict.request_id",
            lambda r, c, e, a, d: validate_risk(
                r,
                RiskVerdict(
                    request_id="wrong",
                    risk_level=RiskLevel.MEDIUM,
                    recommended_decision=PolicyDecision.SANDBOX_CHECK,
                    reason="mock",
                ),
            ),
        ),
        (
            "CheckpointResult.task_id",
            lambda r, c, e, a, d: validate_checkpoint(
                r, c.model_copy(update={"task_id": "wrong"})
            ),
        ),
        (
            "CheckpointResult.step_id",
            lambda r, c, e, a, d: validate_checkpoint(
                r, c.model_copy(update={"step_id": "wrong"})
            ),
        ),
        (
            "CheckpointResult.request_id",
            lambda r, c, e, a, d: validate_checkpoint(
                r, c.model_copy(update={"request_id": "wrong"})
            ),
        ),
        (
            "ToolExecutionResult.task_id",
            lambda r, c, e, a, d: validate_execution(
                r,
                e.model_copy(update={"task_id": "wrong"}),
                checkpoint_id=c.checkpoint_id,
            ),
        ),
        (
            "ToolExecutionResult.step_id",
            lambda r, c, e, a, d: validate_execution(
                r,
                e.model_copy(update={"step_id": "wrong"}),
                checkpoint_id=c.checkpoint_id,
            ),
        ),
        (
            "ToolExecutionResult.request_id",
            lambda r, c, e, a, d: validate_execution(
                r,
                e.model_copy(update={"request_id": "wrong"}),
                checkpoint_id=c.checkpoint_id,
            ),
        ),
        (
            "ToolExecutionResult.checkpoint_id",
            lambda r, c, e, a, d: validate_execution(
                r,
                e.model_copy(update={"checkpoint_id": "wrong"}),
                checkpoint_id=c.checkpoint_id,
            ),
        ),
        (
            "DeepCheckResult.request_id",
            lambda r, c, e, a, d: validate_deep_check(
                r, DeepCheckResult(request_id="wrong", passed=True, reason="mock")
            ),
        ),
        (
            "CommitResult.request_id",
            lambda r, c, e, a, d: validate_commit(
                r,
                c.checkpoint_id,
                CommitResult(
                    request_id="wrong",
                    checkpoint_id=c.checkpoint_id,
                    status=ExecutionStatus.COMMITTED,
                ),
            ),
        ),
        (
            "CommitResult.checkpoint_id",
            lambda r, c, e, a, d: validate_commit(
                r,
                c.checkpoint_id,
                CommitResult(
                    request_id=r.request_id,
                    checkpoint_id="wrong",
                    status=ExecutionStatus.COMMITTED,
                ),
            ),
        ),
        (
            "RollbackResult.request_id",
            lambda r, c, e, a, d: validate_rollback(
                r,
                c.checkpoint_id,
                RollbackResult(
                    request_id="wrong",
                    checkpoint_id=c.checkpoint_id,
                    status=ExecutionStatus.ROLLED_BACK,
                    reason="mock",
                ),
            ),
        ),
        (
            "RollbackResult.checkpoint_id",
            lambda r, c, e, a, d: validate_rollback(
                r,
                c.checkpoint_id,
                RollbackResult(
                    request_id=r.request_id,
                    checkpoint_id="wrong",
                    status=ExecutionStatus.ROLLED_BACK,
                    reason="mock",
                ),
            ),
        ),
        (
            "ApprovalRequest.task_id",
            lambda r, c, e, a, d: validate_approval_request(
                r, a.request_fingerprint, a.model_copy(update={"task_id": "wrong"})
            ),
        ),
        (
            "ApprovalRequest.step_id",
            lambda r, c, e, a, d: validate_approval_request(
                r, a.request_fingerprint, a.model_copy(update={"step_id": "wrong"})
            ),
        ),
        (
            "ApprovalRequest.request_id",
            lambda r, c, e, a, d: validate_approval_request(
                r, a.request_fingerprint, a.model_copy(update={"request_id": "wrong"})
            ),
        ),
        (
            "ApprovalRequest.tool_name",
            lambda r, c, e, a, d: validate_approval_request(
                r, a.request_fingerprint, a.model_copy(update={"tool_name": "wrong"})
            ),
        ),
        (
            "ApprovalRequest.request_fingerprint",
            lambda r, c, e, a, d: validate_approval_request(r, "wrong", a),
        ),
        (
            "ApprovalDecision.approval_id",
            lambda r, c, e, a, d: validate_approval_decision(
                a, d.model_copy(update={"approval_id": "wrong"})
            ),
        ),
        (
            "ApprovalDecision.task_id",
            lambda r, c, e, a, d: validate_approval_decision(
                a, d.model_copy(update={"task_id": "wrong"})
            ),
        ),
        (
            "ApprovalDecision.step_id",
            lambda r, c, e, a, d: validate_approval_decision(
                a, d.model_copy(update={"step_id": "wrong"})
            ),
        ),
        (
            "ApprovalDecision.request_id",
            lambda r, c, e, a, d: validate_approval_decision(
                a, d.model_copy(update={"request_id": "wrong"})
            ),
        ),
    ],
)
def test_each_cross_module_association_mismatch_is_rejected(
    label: str, validate
) -> None:
    with pytest.raises(CorrelationError, match=label):
        validate(*models())
