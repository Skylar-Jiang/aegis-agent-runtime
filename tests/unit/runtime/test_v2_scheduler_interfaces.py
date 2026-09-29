from ra_agent.contracts import (
    ExecutionStatus,
    RollbackPlan,
    RollbackPlanResult,
    TaskGraph,
    TaskGraphResult,
)
from ra_agent.runtime import RollbackPlanExecutor, TaskGraphScheduler


class StubScheduler:
    async def schedule_graph(self, graph: TaskGraph) -> TaskGraphResult:
        return TaskGraphResult(graph_id=graph.graph_id, task_id=graph.task_id)


class StubRollbackExecutor:
    async def execute_rollback_plan(self, plan: RollbackPlan) -> RollbackPlanResult:
        return RollbackPlanResult(
            plan_id=plan.plan_id,
            task_id=plan.task_id,
            rolled_back_request_ids=plan.request_ids,
            failed_request_ids=[],
            status=ExecutionStatus.ROLLED_BACK,
            reason="stub",
        )


def test_v2_scheduler_protocols_are_independently_implementable() -> None:
    assert isinstance(StubScheduler(), TaskGraphScheduler)
    assert isinstance(StubRollbackExecutor(), RollbackPlanExecutor)
