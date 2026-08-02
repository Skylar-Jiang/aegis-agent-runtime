import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ra_agent.audit import InMemoryAuditRecorder
from ra_agent.contracts import (
    ExecutionStatus,
    PolicyDecision,
    RiskLevel,
    SourceType,
    ToolCallRequest,
)
from ra_agent.core.bootstrap import build_mock_container, build_runtime_scheduler


def test_mock_container_provides_all_runtime_dependencies() -> None:
    container = build_mock_container()

    assert isinstance(container.audit_recorder, InMemoryAuditRecorder)
    assert set(container.tool_registry.names()) == {
        "list_dir",
        "read_file",
        "write_file",
        "delete_file",
        "run_shell",
        "download_url",
        "memory_read",
        "memory_write",
        "send_email_dry_run",
    }
    assert container.audit_recorder.events_for("missing") == ()
    assert container.request_registry is not None


def test_mock_containers_do_not_share_mutable_tool_specs() -> None:
    first = build_mock_container()
    second = build_mock_container()

    first.tool_registry.get("list_dir").allowed_paths.append("first-only")

    assert second.tool_registry.get("list_dir").allowed_paths == []


@pytest.mark.asyncio
async def test_mock_tool_call_flows_through_shared_scheduler_and_audit() -> None:
    container = build_mock_container()
    scheduler = build_runtime_scheduler(container)
    request = ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id="request-1",
        tool_name="list_dir",
        arguments={"path": "."},
        objective="inspect workspace",
        context_summary="Need the workspace directory listing.",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
    )

    result = await scheduler.schedule(request)

    assert isinstance(container.audit_recorder, InMemoryAuditRecorder)
    assert result.status is ExecutionStatus.COMMITTED
    assert [
        event.sequence_number for event in container.audit_recorder.events_for("task-1")
    ] == list(range(1, 10))


@pytest.mark.asyncio
async def test_mock_container_preserves_shared_fixture_risk_semantics() -> None:
    fixture_root = Path(__file__).parents[1] / "fixtures"
    expected = {
        "low_list_dir.json": (RiskLevel.LOW, PolicyDecision.FAST_EXECUTE),
        "medium_write_file.json": (RiskLevel.MEDIUM, PolicyDecision.SANDBOX_CHECK),
        "high_sensitive_read.json": (RiskLevel.HIGH, PolicyDecision.REQUEST_APPROVAL),
        "critical_shell.json": (RiskLevel.CRITICAL, PolicyDecision.BLOCK),
    }

    for fixture_name, (risk_level, decision) in expected.items():
        container = build_mock_container()
        scheduler = build_runtime_scheduler(container)
        request = ToolCallRequest.model_validate(
            json.loads((fixture_root / fixture_name).read_text(encoding="utf-8"))
        )

        await scheduler.schedule(request)

        assert isinstance(container.audit_recorder, InMemoryAuditRecorder)
        classified = container.audit_recorder.events_for(request.task_id)[1]
        assert classified.risk_level is risk_level
        assert classified.decision is decision
