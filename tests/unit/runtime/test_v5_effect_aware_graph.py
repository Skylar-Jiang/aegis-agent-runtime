from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ra_agent.contracts import (
    ExecutionStatus,
    SourceType,
    TaskGraph,
    TaskNode,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.runtime.graph_scheduler import RuntimeTaskGraphScheduler


class _TracingRuntime:
    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self.trace: list[tuple[str, str]] = []

    async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.trace.append(("start", request.step_id))
        await asyncio.sleep(0.01)
        self.trace.append(("finish", request.step_id))
        self.active -= 1
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.COMMITTED,
        )


def _node(
    node_id: str,
    tool_name: str,
    arguments: dict[str, object],
    *,
    effect_targets: list[str] | None = None,
) -> TaskNode:
    return TaskNode(
        task_id="v5-graph-task",
        graph_id="v5-graph",
        node_id=node_id,
        request=ToolCallRequest(
            task_id="v5-graph-task",
            step_id=node_id,
            request_id=f"v5-{node_id}",
            tool_name=tool_name,
            arguments=arguments,
            objective="verify effect-aware graph scheduling",
            context_summary="v5 graph test",
            source_type=SourceType.AGENT,
            requested_at=datetime.now(UTC),
        ),
        parallel_safe=True,
        effect_targets=effect_targets or [],
    )


def _run(*nodes: TaskNode) -> _TracingRuntime:
    runtime = _TracingRuntime()
    graph = TaskGraph(
        graph_id="v5-graph",
        task_id="v5-graph-task",
        nodes=list(nodes),
        max_parallelism=2,
    )
    asyncio.run(
        RuntimeTaskGraphScheduler(runtime_scheduler=runtime).schedule_graph(graph)
    )
    return runtime


def test_runtime_serializes_read_write_same_target_without_planner_hints() -> None:
    runtime = _run(
        _node("read", "read_file", {"path": "project/config.yaml"}),
        _node("write", "write_file", {"path": "project/config.yaml", "content": "x"}),
    )

    assert runtime.maximum_active == 1


def test_runtime_serializes_directory_read_and_nested_write_despite_misleading_hints() -> (
    None
):
    runtime = _run(
        _node("scan", "list_dir", {"path": "project"}, effect_targets=["file:other"]),
        _node(
            "write",
            "write_file",
            {"path": "project/subdir/config.yaml", "content": "x"},
            effect_targets=["file:different"],
        ),
    )

    assert runtime.maximum_active == 1


def test_runtime_allows_independent_targets_and_serializes_normalized_alias() -> None:
    independent = _run(
        _node("left", "write_file", {"path": "a.txt", "content": "x"}),
        _node("right", "write_file", {"path": "b.txt", "content": "x"}),
    )
    alias = _run(
        _node("first", "write_file", {"path": "project/./config.yaml", "content": "x"}),
        _node("second", "write_file", {"path": "project/config.yaml", "content": "x"}),
    )

    assert independent.maximum_active == 2
    assert alias.maximum_active == 1
