"""Minimal dependency graph that delegates every tool effect to RuntimeScheduler."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

from ra_agent.contracts import ExecutionStatus, ToolCallRequest, ToolExecutionResult


@dataclass(frozen=True, slots=True)
class TaskGraphNode:
    node_id: str
    request: ToolCallRequest
    dependencies: tuple[str, ...] = ()
    condition: Callable[[dict[str, ToolExecutionResult]], bool] | None = None


@dataclass(slots=True)
class TaskGraphResult:
    results: dict[str, ToolExecutionResult] = field(default_factory=dict)
    blocked: dict[str, str] = field(default_factory=dict)


class TaskGraphRunner:
    """Run independent graph nodes concurrently while preserving dependency safety."""

    def __init__(self, scheduler: object, *, max_parallel: int = 2) -> None:
        if max_parallel <= 0:
            raise ValueError("max_parallel must be positive")
        self._scheduler = scheduler
        self._max_parallel = max_parallel

    async def run(self, nodes: list[TaskGraphNode]) -> TaskGraphResult:
        pending = {node.node_id: node for node in nodes}
        if len(pending) != len(nodes):
            raise ValueError("task graph node ids must be unique")
        result = TaskGraphResult()
        semaphore = asyncio.Semaphore(self._max_parallel)

        while pending:
            ready: list[TaskGraphNode] = []
            for node_id, node in list(pending.items()):
                failed_dependency = any(
                    dependency not in pending and dependency not in result.results
                    for dependency in node.dependencies
                )
                if failed_dependency:
                    result.blocked[node_id] = "dependency_failed_or_waiting"
                    pending.pop(node_id)
                    continue
                if any(dependency in pending for dependency in node.dependencies):
                    continue
                if node.condition is not None and not node.condition(result.results):
                    result.blocked[node_id] = "condition_not_met"
                    pending.pop(node_id)
                    continue
                ready.append(node)

            if not ready:
                for node_id in pending:
                    result.blocked[node_id] = "unresolved_dependency_or_cycle"
                break

            for node in ready:
                pending.pop(node.node_id)

            async def schedule(node: TaskGraphNode) -> tuple[str, ToolExecutionResult]:
                async with semaphore:
                    scheduler = self._scheduler
                    execution = await scheduler.schedule(node.request)  # type: ignore[attr-defined]
                    return node.node_id, execution

            completed = await asyncio.gather(*(schedule(node) for node in ready))
            for node_id, execution in completed:
                if execution.status is ExecutionStatus.COMMITTED:
                    result.results[node_id] = execution
                else:
                    result.blocked[node_id] = execution.status.value
        return result
