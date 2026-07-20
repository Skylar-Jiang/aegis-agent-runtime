import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from ra_agent.contracts import (
    CONTRACT_VERSION,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    AuditEvent,
    AuditEventType,
    DeepCheckResult,
    ExperimentMode,
    ExecutionStatus,
    MemoryStatus,
    PermissionCheckResult,
    PermissionDecision,
    PermissionStatus,
    PermissionType,
    PolicyDecision,
    RiskLevel,
    RiskVerdict,
    SourceType,
    TaskCreateRequest,
    TaskResponse,
    TaskStep,
    ToolCallRequest,
    ToolExecutionResult,
)


def test_contract_version_is_v03() -> None:
    assert CONTRACT_VERSION == "0.3"


def test_phase3_contracts_capture_checks_memory_experiments_and_dependencies() -> None:
    from ra_agent.contracts import PostCheckResult, PreCheckResult

    pre_check = PreCheckResult(request_id="request-1", passed=True, reason="safe")
    post_check = PostCheckResult(request_id="request-1", passed=True, reason="safe")
    step = TaskStep(
        task_id="task-1",
        step_id="step-1",
        description="download logs",
        tool_name="download_url",
        arguments={"url": "https://example.test/logs"},
        dependencies=["step-0"],
        required_permissions=[PermissionType.NETWORK_DOWNLOAD],
    )

    assert pre_check.signals == []
    assert post_check.signals == []
    assert MemoryStatus.PENDING.value == "PENDING"
    assert ExperimentMode.ADAPTIVE_RUNTIME.value == "ADAPTIVE_RUNTIME"
    assert step.dependencies == ["step-0"]


def test_task_request_and_tool_call_serialize_to_json() -> None:
    task = TaskCreateRequest(objective="summarize README")
    call = ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id="request-1",
        tool_name="read_file",
        arguments={"path": "README.md"},
        objective="summarize README",
        context_summary="The user asked for the repository overview.",
        source_type=SourceType.USER,
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
            objective="read a file",
            context_summary="The requested path is part of the task.",
            source_type=SourceType.AGENT,
            requested_at=datetime.now(),
        )


def test_public_contract_datetimes_normalize_to_utc() -> None:
    plus_eight = timezone(timedelta(hours=8))
    local_time = datetime(2026, 7, 12, 8, 30, tzinfo=plus_eight)
    expected = datetime(2026, 7, 12, 0, 30, tzinfo=UTC)
    request = ToolCallRequest(
        task_id="task-utc",
        step_id="step-utc",
        request_id="request-utc",
        tool_name="list_dir",
        objective="inspect",
        context_summary="normalize request time",
        source_type=SourceType.USER,
        requested_at=local_time,
    )
    execution = ToolExecutionResult(
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        status=ExecutionStatus.COMMITTED,
        started_at=local_time,
        finished_at=local_time,
    )
    event = AuditEvent(
        event_id="event-utc",
        task_id=request.task_id,
        sequence_number=1,
        event_type=AuditEventType.TOOL_REQUESTED,
        timestamp=local_time,
        actor="test",
        status="recorded",
        summary="utc",
    )
    task = TaskResponse(
        task_id=request.task_id,
        objective="inspect",
        status="active",
        created_at=local_time,
    )
    approval = ApprovalRequest(
        approval_id="approval-1",
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        tool_name=request.tool_name,
        request_fingerprint="fingerprint-utc",
        reason="test",
        requested_at=local_time,
        expires_at=local_time,
    )
    decision = ApprovalDecision(
        approval_id=approval.approval_id,
        task_id=approval.task_id,
        step_id=approval.step_id,
        request_id=approval.request_id,
        status=ApprovalStatus.GRANTED,
        granted=True,
        decided_by="test",
        decided_at=local_time,
        reason="test",
    )

    assert request.requested_at == expected
    assert execution.started_at == expected
    assert execution.finished_at == expected
    assert event.timestamp == expected
    assert task.created_at == expected
    assert approval.requested_at == expected
    assert approval.expires_at == expected
    assert decision.decided_at == expected
    assert request.model_dump(mode="json")["requested_at"] == "2026-07-12T00:30:00Z"


def test_utc_input_remains_utc_and_execution_rejects_naive_datetime() -> None:
    utc_time = datetime(2026, 7, 12, 0, 30, tzinfo=UTC)
    request = ToolCallRequest(
        task_id="task-utc",
        step_id="step-utc",
        request_id="request-utc",
        tool_name="list_dir",
        objective="inspect",
        context_summary="keep utc",
        source_type=SourceType.USER,
        requested_at=utc_time,
    )

    assert request.requested_at is utc_time
    with pytest.raises(ValidationError):
        ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.SUCCESS,
            started_at=datetime.now(),
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


def test_v02_contracts_expose_safe_defaults_and_runtime_metadata() -> None:
    request = ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id="request-1",
        tool_name="list_dir",
        objective="inspect workspace",
        context_summary="Need the top-level directory listing.",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )
    verdict = RiskVerdict(
        request_id=request.request_id,
        risk_level=RiskLevel.LOW,
        recommended_decision=PolicyDecision.FAST_EXECUTE,
        reason="read-only listing",
    )
    deep_check = DeepCheckResult(
        request_id=request.request_id, passed=True, reason="safe"
    )
    result = ToolExecutionResult(
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        status=ExecutionStatus.COMMITTED,
    )

    assert request.arguments == {}
    assert verdict.signals == []
    assert verdict.matched_rules == []
    assert verdict.requires_deep_check is False
    assert verdict.requires_checkpoint is False
    assert deep_check.signals == []
    assert result.artifacts == []
    assert result.pending_changes == []
    assert result.checkpoint_id is None
    assert result.sandbox_path is None
    assert result.error_code is None
    payloads = [
        request.model_dump(mode="json"),
        verdict.model_dump(mode="json"),
        deep_check.model_dump(mode="json"),
        result.model_dump(mode="json"),
    ]
    assert all(payload["request_id"] == request.request_id for payload in payloads)


def test_permission_check_result_combines_multiple_trusted_decisions() -> None:
    result = PermissionCheckResult(
        request_id="request-1",
        decisions=[
            PermissionDecision(
                request_id="request-1",
                permission=PermissionType.FILE_READ,
                status=PermissionStatus.GRANTED,
                reason="granted",
            ),
            PermissionDecision(
                request_id="request-1",
                permission=PermissionType.SENSITIVE_READ,
                status=PermissionStatus.PENDING,
                reason="approval required",
            ),
        ],
        allowed=False,
        requires_approval=True,
        reason="one permission requires approval",
    )

    assert len(result.decisions) == 2
    assert result.allowed is False


def test_shared_tool_call_request_fixtures_follow_contract_v02() -> None:
    fixture_root = Path(__file__).parents[1] / "fixtures"
    fixture_names = {
        "low_list_dir.json",
        "medium_write_file.json",
        "high_sensitive_read.json",
        "critical_shell.json",
    }

    requests = {
        path.name: ToolCallRequest.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
        for path in fixture_root.glob("*.json")
    }

    assert set(requests) == fixture_names
    assert requests["low_list_dir.json"].tool_name == "list_dir"
    assert requests["medium_write_file.json"].tool_name == "write_file"
    assert requests["high_sensitive_read.json"].arguments["path"] == ".env"
    assert "rm -rf" in requests["critical_shell.json"].arguments["command"]
