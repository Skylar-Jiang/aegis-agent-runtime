import asyncio
from datetime import UTC, datetime

from ra_agent.contracts import (
    ExecutionStatus,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.runtime.task_graph import TaskGraphNode, TaskGraphRunner


class Scheduler:
    def __init__(self, statuses: dict[str, ExecutionStatus]) -> None:
        self.statuses = statuses
        self.calls: list[str] = []

    async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
        self.calls.append(request.step_id)
        await asyncio.sleep(0)
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=self.statuses[request.step_id],
        )


def _node(node_id: str, *dependencies: str) -> TaskGraphNode:
    return TaskGraphNode(
        node_id=node_id,
        dependencies=dependencies,
        request=ToolCallRequest(
            task_id="task-graph",
            step_id=node_id,
            request_id=f"request-{node_id}",
            tool_name="list_dir",
            arguments={"path": "workspace"},
            objective="inspect workspace",
            context_summary="task graph test",
            source_type=SourceType.AGENT,
            requested_at=datetime.now(UTC),
        ),
    )


def test_failure_blocks_only_descendants_and_keeps_independent_branch_running() -> None:
    scheduler = Scheduler(
        {"first": ExecutionStatus.FAILED, "independent": ExecutionStatus.COMMITTED}
    )
    result = asyncio.run(
        TaskGraphRunner(scheduler).run(
            [_node("first"), _node("child", "first"), _node("independent")]
        )
    )

    assert set(scheduler.calls) == {"first", "independent"}
    assert set(result.results) == {"independent"}
    assert result.blocked["child"] == "dependency_failed_or_waiting"


def test_waiting_approval_blocks_descendant_but_not_other_ready_nodes() -> None:
    scheduler = Scheduler(
        {"approval": ExecutionStatus.WAITING_APPROVAL, "other": ExecutionStatus.COMMITTED}
    )
    result = asyncio.run(
        TaskGraphRunner(scheduler).run(
            [_node("approval"), _node("after", "approval"), _node("other")]
        )
    )

    assert set(result.results) == {"other"}
    assert result.blocked["approval"] == "WAITING_APPROVAL"
    assert result.blocked["after"] == "dependency_failed_or_waiting"
