from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ra_agent.contracts import (
    AuditEvent,
    AuditEventType,
    RiskLevel,
    TaskCreateRequest,
    ToolCallRequest,
)


def test_task_request_and_tool_call_serialize_to_json() -> None:
    task = TaskCreateRequest(objective="summarize README")
    call = ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id="request-1",
        tool_name="read_file",
        arguments={"path": "README.md"},
        requested_at=datetime.now(UTC),
    )

    assert task.model_dump(mode="json")["objective"] == "summarize README"
    assert call.model_dump(mode="json")["request_id"] == "request-1"


def test_contract_rejects_naive_datetime() -> None:
    with pytest.raises(ValidationError):
        ToolCallRequest(
            task_id="task-1",
            step_id="step-1",
            request_id="request-1",
            tool_name="read_file",
            arguments={},
            requested_at=datetime.now(),
        )


def test_audit_event_serializes_frozen_enum_values() -> None:
    event = AuditEvent(
        event_id="event-1",
        task_id="task-1",
        step_id="step-1",
        request_id="request-1",
        sequence_number=1,
        event_type=AuditEventType.RISK_CLASSIFIED,
        timestamp=datetime.now(UTC),
        actor="runtime",
        status="completed",
        risk_level=RiskLevel.LOW,
        summary="risk classified",
    )

    payload = event.model_dump(mode="json")
    assert payload["event_type"] == "RISK_CLASSIFIED"
    assert payload["risk_level"] == "LOW"
