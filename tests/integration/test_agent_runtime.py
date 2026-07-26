from datetime import UTC, datetime

import pytest

from ra_agent.agent import AgentRunStatus, AgentRuntime, MockPlanner, build_graph
from ra_agent.contracts import (
    ExecutionStatus,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
)


def make_request(index: int) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-agent",
        step_id=f"step-{index}",
        request_id=f"request-{index}",
        tool_name="list_dir",
        arguments={"path": "."},
        objective="inspect workspace",
        context_summary=f"fixed mock step {index}",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


class TrackingScheduler:
    def __init__(self, statuses: list[ExecutionStatus]) -> None:
        self.statuses = statuses
        self.requests: list[ToolCallRequest] = []

    async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
        self.requests.append(request)
        status = self.statuses[len(self.requests) - 1]
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=status,
        )


class MismatchedScheduler(TrackingScheduler):
    async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
        result = await super().schedule(request)
        return result.model_copy(update={"request_id": "wrong-request"})


class FailingPlanner:
    async def plan(self, task_id: str, objective: str) -> list[ToolCallRequest]:
        raise RuntimeError("planner unavailable")


class TwoTurnPlanner:
    def __init__(self) -> None:
        self.requests = [make_request(1), make_request(2)]

    async def plan(self, task_id: str, objective: str) -> list[ToolCallRequest]:
        return []

    async def next_request(
        self, task_id: str, objective: str, results: list[ToolExecutionResult]
    ) -> ToolCallRequest | None:
        return self.requests[len(results)] if len(results) < len(self.requests) else None


@pytest.mark.asyncio
async def test_mock_planner_returns_fixed_tool_requests_without_execution() -> None:
    requests = [make_request(1), make_request(2)]
    planner = MockPlanner(requests)

    planned = await planner.plan("task-agent", "inspect workspace")

    assert planned == requests


@pytest.mark.asyncio
async def test_agent_routes_every_request_through_scheduler_and_accumulates_commits() -> (
    None
):
    requests = [make_request(1), make_request(2)]
    scheduler = TrackingScheduler(
        [ExecutionStatus.COMMITTED, ExecutionStatus.COMMITTED]
    )
    runtime = AgentRuntime(planner=MockPlanner(requests), scheduler=scheduler)

    state = await runtime.run("task-agent", "inspect workspace")

    assert scheduler.requests == requests
    assert len(state.results) == 2
    assert state.status is AgentRunStatus.COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("execution_status", "agent_status"),
    [
        (ExecutionStatus.WAITING_APPROVAL, "WAITING_APPROVAL"),
        (ExecutionStatus.BLOCKED, "BLOCKED"),
        (ExecutionStatus.FAILED, "FAILED"),
    ],
)
async def test_agent_stops_after_waiting_blocked_or_failed_result(
    execution_status: ExecutionStatus, agent_status: str
) -> None:
    scheduler = TrackingScheduler([execution_status, ExecutionStatus.COMMITTED])
    runtime = AgentRuntime(
        planner=MockPlanner([make_request(1), make_request(2)]), scheduler=scheduler
    )

    state = await runtime.run("task-agent", "inspect workspace")

    assert len(scheduler.requests) == 1
    assert len(state.results) == 1
    assert state.status is getattr(AgentRunStatus, agent_status)


def test_build_graph_returns_minimal_agent_runtime_instead_of_placeholder() -> None:
    planner = MockPlanner([make_request(1)])
    scheduler = TrackingScheduler([ExecutionStatus.COMMITTED])

    runtime = build_graph(planner=planner, scheduler=scheduler)

    assert isinstance(runtime, AgentRuntime)


@pytest.mark.asyncio
async def test_agent_rejects_planner_request_for_another_task_before_scheduling() -> (
    None
):
    request = make_request(1).model_copy(update={"task_id": "wrong-task"})
    scheduler = TrackingScheduler([ExecutionStatus.COMMITTED])
    runtime = AgentRuntime(planner=MockPlanner([request]), scheduler=scheduler)

    state = await runtime.run("task-agent", "inspect workspace")

    assert state.status is AgentRunStatus.FAILED
    assert scheduler.requests == []


@pytest.mark.asyncio
async def test_agent_rejects_mismatched_scheduler_result() -> None:
    runtime = AgentRuntime(
        planner=MockPlanner([make_request(1)]),
        scheduler=MismatchedScheduler([ExecutionStatus.COMMITTED]),
    )

    state = await runtime.run("task-agent", "inspect workspace")

    assert state.status is AgentRunStatus.FAILED
    assert state.results[0].error_code == "CORRELATION_MISMATCH"


@pytest.mark.asyncio
async def test_agent_planner_failure_never_schedules_a_tool() -> None:
    scheduler = TrackingScheduler([ExecutionStatus.COMMITTED])

    state = await AgentRuntime(planner=FailingPlanner(), scheduler=scheduler).run(
        "task-agent", "inspect workspace"
    )

    assert state.status is AgentRunStatus.FAILED
    assert state.results == []
    assert scheduler.requests == []
    assert state.failure_code == "PLANNER_FAILED"
    assert state.failure_reason == "RuntimeError"


@pytest.mark.asyncio
async def test_agent_iteratively_replans_through_scheduler_until_model_stops() -> None:
    scheduler = TrackingScheduler([ExecutionStatus.COMMITTED, ExecutionStatus.COMMITTED])
    planner = TwoTurnPlanner()

    state = await AgentRuntime(planner=planner, scheduler=scheduler).run(
        "task-agent", "inspect workspace"
    )

    assert state.status is AgentRunStatus.COMPLETED
    assert scheduler.requests == planner.requests
