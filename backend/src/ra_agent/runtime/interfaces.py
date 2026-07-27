from typing import Protocol, runtime_checkable

from ra_agent.contracts import RollbackPlan, RollbackPlanResult, TaskGraph, TaskGraphResult


@runtime_checkable
class TaskGraphScheduler(Protocol):
    """V2 graph scheduling boundary; implementation remains owned by the group lead."""

    async def schedule_graph(self, graph: TaskGraph) -> TaskGraphResult: ...


@runtime_checkable
class RollbackPlanExecutor(Protocol):
    """V2 selective rollback boundary; execution implementations remain effect-owned."""

    async def execute_rollback_plan(self, plan: RollbackPlan) -> RollbackPlanResult: ...
