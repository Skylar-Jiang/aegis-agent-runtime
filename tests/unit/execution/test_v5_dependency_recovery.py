from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ra_agent.contracts import (
    EffectRecord,
    EffectStatus,
    ExecutionStatus,
    SourceType,
    TaskGraph,
    TaskNode,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution.effect_store import FilesystemEffectStore
from ra_agent.execution.recovery import DependencyRecoveryPlanner
from ra_agent.runtime.graph_scheduler import RuntimeTaskGraphScheduler


def _effect(effect_id: str, request_id: str) -> EffectRecord:
    return EffectRecord(
        effect_id=effect_id,
        task_id="recovery-task",
        step_id=f"step-{effect_id}",
        request_id=request_id,
        kind="MEMORY_WRITE",
        target_ref=f"memory:{effect_id}",
        status=EffectStatus.COMMITTED,
        created_at=datetime.now(UTC),
    )


def test_recovery_plan_selects_persisted_descendant_closure_and_preserves_unrelated(
    tmp_path,
) -> None:
    async def exercise() -> None:
        store = FilesystemEffectStore(tmp_path / "effects")
        upstream = await store.register(_effect("upstream", "request-upstream"))
        derived = await store.register(_effect("derived", "request-derived"))
        unrelated = await store.register(_effect("unrelated", "request-unrelated"))
        await store.attach_parent_effect_ids(derived.effect_id, [upstream.effect_id])

        plan = await DependencyRecoveryPlanner(store).plan(
            task_id="recovery-task",
            failed_effect_ids=[upstream.effect_id],
            trigger="v5-test-failure",
        )

        assert plan.affected_effect_ids == ["derived", "upstream"]
        assert plan.rollback_effect_ids == ["derived", "upstream"]
        assert plan.preserve_effect_ids == [unrelated.effect_id]
        assert plan.to_rollback_plan().effect_ids == ["derived", "upstream"]

    asyncio.run(exercise())


def test_lineage_rejects_cross_task_parent(tmp_path) -> None:
    async def exercise() -> None:
        store = FilesystemEffectStore(tmp_path / "effects")
        parent = await store.register(_effect("parent", "request-parent"))
        child = await store.register(
            _effect("child", "request-child").model_copy(
                update={"task_id": "other-task"}
            )
        )

        try:
            await store.attach_parent_effect_ids(child.effect_id, [parent.effect_id])
        except ValueError as error:
            assert "same task" in str(error)
        else:
            raise AssertionError("cross-task lineage must be rejected")

    asyncio.run(exercise())


def test_graph_persists_explicit_dependency_as_effect_lineage(tmp_path) -> None:
    async def exercise() -> None:
        store = FilesystemEffectStore(tmp_path / "effects")
        upstream = await store.register(_effect("upstream", "request-upstream"))
        derived = await store.register(_effect("derived", "request-derived"))

        def node(node_id: str, request_id: str, dependencies: list[str]) -> TaskNode:
            return TaskNode(
                task_id="recovery-task",
                graph_id="recovery-graph",
                node_id=node_id,
                request=ToolCallRequest(
                    task_id="recovery-task",
                    step_id=node_id,
                    request_id=request_id,
                    tool_name="memory_write",
                    arguments={"key": node_id, "value": "test"},
                    objective="persist lineage",
                    context_summary="v5 lineage integration test",
                    source_type=SourceType.AGENT,
                    requested_at=datetime.now(UTC),
                ),
                dependencies=dependencies,
                parallel_safe=True,
            )

        class Runtime:
            async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
                return ToolExecutionResult(
                    task_id=request.task_id,
                    step_id=request.step_id,
                    request_id=request.request_id,
                    status=ExecutionStatus.COMMITTED,
                )

        await RuntimeTaskGraphScheduler(
            runtime_scheduler=Runtime(), effect_store=store
        ).schedule_graph(
            TaskGraph(
                graph_id="recovery-graph",
                task_id="recovery-task",
                nodes=[
                    node("upstream", upstream.request_id, []),
                    node("derived", derived.request_id, ["upstream"]),
                ],
                max_parallelism=2,
            )
        )
        assert (await store.get(derived.effect_id)).parent_effect_ids == [
            upstream.effect_id
        ]

    asyncio.run(exercise())


def test_graph_failure_uses_lineage_closure_for_automatic_rollback(tmp_path) -> None:
    async def exercise() -> None:
        store = FilesystemEffectStore(tmp_path / "effects")
        upstream = await store.register(_effect("upstream", "request-upstream"))
        derived = await store.register(_effect("derived", "request-derived"))
        unrelated = await store.register(_effect("unrelated", "request-unrelated"))
        await store.attach_parent_effect_ids(derived.effect_id, [upstream.effect_id])

        def node(
            node_id: str, request_id: str, dependencies: list[str] | None = None
        ) -> TaskNode:
            return TaskNode(
                task_id="recovery-task",
                graph_id="failure-graph",
                node_id=node_id,
                request=ToolCallRequest(
                    task_id="recovery-task",
                    step_id=node_id,
                    request_id=request_id,
                    tool_name="memory_write",
                    arguments={"key": node_id, "value": "test"},
                    objective="derive rollback closure",
                    context_summary="v5 recovery integration test",
                    source_type=SourceType.AGENT,
                    requested_at=datetime.now(UTC),
                ),
                dependencies=dependencies or [],
                parallel_safe=True,
            )

        class Runtime:
            async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
                status = (
                    ExecutionStatus.FAILED
                    if request.request_id == upstream.request_id
                    else ExecutionStatus.COMMITTED
                )
                return ToolExecutionResult(
                    task_id=request.task_id,
                    step_id=request.step_id,
                    request_id=request.request_id,
                    status=status,
                    error="injected failure"
                    if status is ExecutionStatus.FAILED
                    else None,
                )

        class RollbackRecorder:
            plans = []

            async def execute_rollback_plan(self, plan):
                self.plans.append(plan)
                from ra_agent.contracts import RollbackPlanResult

                return RollbackPlanResult(
                    plan_id=plan.plan_id,
                    task_id=plan.task_id,
                    rolled_back_request_ids=["request-derived", "request-upstream"],
                    status=ExecutionStatus.SUCCESS,
                    reason="recorded",
                )

        rollback = RollbackRecorder()
        await RuntimeTaskGraphScheduler(
            runtime_scheduler=Runtime(), effect_store=store, rollback_executor=rollback
        ).schedule_graph(
            TaskGraph(
                graph_id="failure-graph",
                task_id="recovery-task",
                nodes=[
                    node("upstream", upstream.request_id),
                    node("derived", derived.request_id, ["upstream"]),
                    node("unrelated", unrelated.request_id),
                ],
                max_parallelism=2,
            )
        )
        assert rollback.plans[0].effect_ids == ["derived", "upstream"]

    asyncio.run(exercise())
