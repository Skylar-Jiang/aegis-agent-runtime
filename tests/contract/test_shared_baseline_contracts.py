from datetime import UTC, datetime, timedelta, timezone

from ra_agent import contracts
from ra_agent.contracts import ApprovalDecision, ApprovalRequest, ExecutionStatus


def test_approval_contracts_bind_request_and_serialize_explicit_status() -> None:
    approval_status = getattr(contracts, "ApprovalStatus", None)
    assert approval_status is not None
    local_tz = timezone(timedelta(hours=8))
    requested_at = datetime(2026, 7, 12, 16, 0, tzinfo=local_tz)
    approval = ApprovalRequest(
        approval_id="approval-1",
        task_id="task-1",
        step_id="step-1",
        request_id="request-1",
        tool_name="delete_file",
        request_fingerprint="fingerprint-1",
        reason="high risk",
        requested_at=requested_at,
        expires_at=requested_at + timedelta(minutes=10),
        status=approval_status.PENDING,
    )
    decision = ApprovalDecision(
        approval_id=approval.approval_id,
        task_id=approval.task_id,
        step_id=approval.step_id,
        request_id=approval.request_id,
        status=approval_status.GRANTED,
        decided_by="reviewer",
        decided_at=requested_at,
        reason="approved for sandbox execution",
    )

    assert approval.requested_at == datetime(2026, 7, 12, 8, 0, tzinfo=UTC)
    assert approval.model_dump(mode="json")["status"] == "PENDING"
    assert decision.model_dump(mode="json")["status"] == "GRANTED"
    assert decision.model_dump(mode="json")["decided_at"].endswith("Z")


def test_execution_status_has_normal_waiting_approval_state() -> None:
    assert ExecutionStatus.WAITING_APPROVAL.value == "WAITING_APPROVAL"
