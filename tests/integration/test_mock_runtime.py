import asyncio
from datetime import UTC, datetime

from ra_agent.audit import InMemoryAuditEventSink
from ra_agent.contracts import ExecutionStatus, ToolCallRequest
from ra_agent.execution import MockToolExecutor
from ra_agent.runtime.scheduler import RuntimeScheduler
from ra_agent.security import MockPermissionGate, MockPolicyEngine, MockRiskClassifier


def test_mock_tool_call_flows_through_scheduler_and_audit() -> None:
    async def exercise() -> None:
        audit = InMemoryAuditEventSink()
        scheduler = RuntimeScheduler(
            classifier=MockRiskClassifier(),
            policy=MockPolicyEngine(),
            permission_gate=MockPermissionGate(),
            executor=MockToolExecutor(),
            audit=audit,
        )
        request = ToolCallRequest(
            task_id="task-1",
            step_id="step-1",
            request_id="request-1",
            tool_name="list_dir",
            arguments={"path": "."},
            requested_at=datetime.now(UTC),
        )

        result = await scheduler.schedule(request)

        assert result.status is ExecutionStatus.SUCCESS
        assert [event.sequence_number for event in audit.events_for("task-1")] == [
            1,
            2,
            3,
        ]

    asyncio.run(exercise())
