import asyncio
from datetime import UTC, datetime

import pytest

from ra_agent.audit import InMemoryAuditRecorder
from ra_agent.contracts import (
    EffectRecord,
    EffectStatus,
    ExecutionStatus,
    RollbackPlan,
    RollbackPlanResult,
    SourceType,
    TaskGraph,
    TaskGraphResult,
    TaskNode,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.runtime.graph_scheduler import RuntimeTaskGraphScheduler


def _node(
    node_id: str,
    *,
    dependencies: list[str] | None = None,
    parallel_safe: bool = True,
    effect_targets: list[str] | None = None,
    tool_name: str = "read_file",
    arguments: dict[str, object] | None = None,
) -> TaskNode:
    return TaskNode(
        task_id="task-graph-v2",
        graph_id="graph-v2",
        node_id=node_id,
        request=ToolCallRequest(
            task_id="task-graph-v2",
            step_id=node_id,
            request_id=f"request-{node_id}",
            tool_name=tool_name,
            arguments=arguments or {"path": f"{node_id}.txt"},
            objective="verify graph scheduling",
            context_summary="v2 graph scheduler test",
            source_type=SourceType.AGENT,
            requested_at=datetime.now(UTC),
        ),
        dependencies=dependencies or [],
        parallel_safe=parallel_safe,
        effect_targets=effect_targets or [],
    )


class _ControlledRuntimeScheduler:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.finished: list[str] = []
        self._first_started = asyncio.Event()
        self._second_started = asyncio.Event()

    async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
        self.started.append(request.step_id)
        if request.step_id == "first":
            self._first_started.set()
            await self._second_started.wait()
        elif request.step_id == "second":
            await self._first_started.wait()
            self._second_started.set()
        self.finished.append(request.step_id)
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.COMMITTED,
        )


def test_independent_parallel_safe_nodes_run_concurrently() -> None:
    runtime = _ControlledRuntimeScheduler()
    graph = TaskGraph(
        graph_id="graph-v2",
        task_id="task-graph-v2",
        max_parallelism=2,
        nodes=[_node("first"), _node("second")],
    )

    result = asyncio.run(RuntimeTaskGraphScheduler(runtime_scheduler=runtime).schedule_graph(graph))

    assert runtime.started == ["first", "second"]
    assert set(result.node_results) == {"first", "second"}


def test_dependency_is_not_scheduled_before_its_parent_completes() -> None:
    class Scheduler:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
            self.calls.append(request.step_id)
            return ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=ExecutionStatus.COMMITTED,
            )

    runtime = Scheduler()
    graph = TaskGraph(
        graph_id="graph-v2",
        task_id="task-graph-v2",
        max_parallelism=2,
        nodes=[_node("parent"), _node("child", dependencies=["parent"])],
    )

    asyncio.run(RuntimeTaskGraphScheduler(runtime_scheduler=runtime).schedule_graph(graph))

    assert runtime.calls == ["parent", "child"]


def test_shared_effect_target_is_serialized() -> None:
    class Scheduler:
        def __init__(self) -> None:
            self.active = 0
            self.maximum_active = 0

        async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            return ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=ExecutionStatus.COMMITTED,
            )

    runtime = Scheduler()
    graph = TaskGraph(
        graph_id="graph-v2",
        task_id="task-graph-v2",
        max_parallelism=2,
        nodes=[
            _node("left", effect_targets=["file:demo.txt"]),
            _node("right", effect_targets=["file:demo.txt"]),
        ],
    )

    asyncio.run(RuntimeTaskGraphScheduler(runtime_scheduler=runtime).schedule_graph(graph))

    assert runtime.maximum_active == 1


def test_waiting_approval_pauses_only_its_descendant_then_resumes_it() -> None:
    class Scheduler:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
            self.calls.append(request.step_id)
            if request.step_id == "approval":
                return ToolExecutionResult(
                    task_id=request.task_id,
                    step_id=request.step_id,
                    request_id=request.request_id,
                    status=ExecutionStatus.WAITING_APPROVAL,
                    output={"approval_id": "approval-1"},
                )
            return ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=ExecutionStatus.COMMITTED,
            )

        async def resume_after_approval(
            self, request: ToolCallRequest, approval_id: str
        ) -> ToolExecutionResult:
            assert approval_id == "approval-1"
            return ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=ExecutionStatus.COMMITTED,
            )

    runtime = Scheduler()
    scheduler = RuntimeTaskGraphScheduler(runtime_scheduler=runtime)
    graph = TaskGraph(
        graph_id="graph-v2",
        task_id="task-graph-v2",
        max_parallelism=2,
        nodes=[
            _node("approval", effect_targets=["file:locked.txt"]),
            _node("child", dependencies=["approval"]),
            _node("independent"),
        ],
    )

    first = asyncio.run(scheduler.schedule_graph(graph))
    resumed = asyncio.run(scheduler.resume_after_approval("graph-v2", "approval-1"))

    assert set(first.node_results) == {"independent"}
    assert first.blocked_nodes["approval"] == "WAITING_APPROVAL"
    assert first.blocked_nodes["child"] == "dependency_waiting_approval"
    assert set(resumed.node_results) == {"approval", "child", "independent"}
    assert runtime.calls == ["approval", "independent", "child"]


def test_cancelled_graph_cancels_waiting_node_and_rejects_approval_resume() -> None:
    class Scheduler:
        def __init__(self) -> None:
            self.resume_calls = 0

        async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
            return ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=ExecutionStatus.WAITING_APPROVAL,
                output={"approval_id": "approval-1"},
            )

        async def cancel_task(self, task_id: str) -> None:
            assert task_id == "task-graph-v2"

        async def resume_after_approval(
            self, request: ToolCallRequest, approval_id: str
        ) -> ToolExecutionResult:
            self.resume_calls += 1
            raise AssertionError("a cancelled graph must not resume")

    async def run() -> tuple[TaskGraphResult, TaskGraphResult, Scheduler]:
        runtime = Scheduler()
        scheduler = RuntimeTaskGraphScheduler(runtime_scheduler=runtime)
        graph = TaskGraph(
            graph_id="graph-v2",
            task_id="task-graph-v2",
            max_parallelism=1,
            nodes=[_node("approval")],
        )
        waiting = await scheduler.schedule_graph(graph)
        cancelled = await scheduler.cancel_graph(graph.graph_id)
        with pytest.raises(RuntimeError, match="graph is cancelled"):
            await scheduler.resume_after_approval(graph.graph_id, "approval-1")
        return waiting, cancelled, runtime

    waiting, cancelled, runtime = asyncio.run(run())

    assert waiting.finished_at is None
    assert waiting.blocked_nodes["approval"] == "WAITING_APPROVAL"
    assert cancelled.finished_at is not None
    assert cancelled.blocked_nodes["approval"] == "graph_cancelled"
    assert runtime.resume_calls == 0


def test_failure_blocks_all_descendants_without_executing_them() -> None:
    class Scheduler:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
            self.calls.append(request.step_id)
            return ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=ExecutionStatus.FAILED,
                error="controlled failure",
            )

    runtime = Scheduler()
    graph = TaskGraph(
        graph_id="graph-v2",
        task_id="task-graph-v2",
        max_parallelism=1,
        nodes=[
            _node("a"),
            _node("b", dependencies=["a"]),
            _node("c", dependencies=["b"]),
        ],
    )

    result = asyncio.run(RuntimeTaskGraphScheduler(runtime_scheduler=runtime).schedule_graph(graph))

    assert runtime.calls == ["a"]
    assert result.blocked_nodes["b"] == "dependency_failed"
    assert result.blocked_nodes["c"] == "dependency_failed"


def test_builtin_write_target_ignores_planner_declared_effect_targets() -> None:
    class Scheduler:
        def __init__(self) -> None:
            self.first_started = asyncio.Event()
            self.second_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
            if request.step_id == "first":
                self.first_started.set()
                await self.release_first.wait()
            else:
                self.second_started.set()
            return ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=ExecutionStatus.COMMITTED,
            )

    async def run() -> Scheduler:
        runtime = Scheduler()
        graph = TaskGraph(
            graph_id="graph-v2",
            task_id="task-graph-v2",
            max_parallelism=2,
            nodes=[
                _node(
                    "first",
                    tool_name="write_file",
                    arguments={"path": "folder/./same.txt", "content": "first"},
                    effect_targets=["file:planner-first"],
                ),
                _node(
                    "second",
                    tool_name="write_file",
                    arguments={"path": "folder/same.txt", "content": "second"},
                    effect_targets=["file:planner-second"],
                ),
            ],
        )
        task = asyncio.create_task(
            RuntimeTaskGraphScheduler(runtime_scheduler=runtime).schedule_graph(graph)
        )
        await runtime.first_started.wait()
        await asyncio.sleep(0)
        assert not runtime.second_started.is_set()
        runtime.release_first.set()
        await task
        return runtime

    runtime = asyncio.run(run())

    assert runtime.second_started.is_set()


def test_builtin_download_target_is_normalized_without_planner_input() -> None:
    scheduler = RuntimeTaskGraphScheduler(runtime_scheduler=object())

    target = scheduler._node_targets(
        _node(
            "download",
            tool_name="download_url",
            arguments={"url": "HTTPS://Example.test:443/archive#fragment"},
            effect_targets=["download:planner-declared-target"],
        )
    )

    assert target == {"download:https://example.test:443/archive"}


def test_branch_failure_rolls_back_only_its_committed_effect() -> None:
    class Scheduler:
        async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
            return ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=(
                    ExecutionStatus.FAILED
                    if request.step_id == "failed"
                    else ExecutionStatus.COMMITTED
                ),
                error="controlled failure" if request.step_id == "failed" else None,
            )

    class EffectStore:
        async def list_by_task_id(self, task_id: str) -> tuple[EffectRecord, ...]:
            return (
                EffectRecord(
                    effect_id="effect-failed",
                    task_id=task_id,
                    step_id="failed",
                    request_id="request-failed",
                    kind="FILE_WRITE",
                    target_ref="file:failed.txt",
                    status=EffectStatus.COMMITTED,
                    checkpoint_id="checkpoint-failed",
                    created_at=datetime.now(UTC),
                ),
                EffectRecord(
                    effect_id="effect-independent",
                    task_id=task_id,
                    step_id="independent",
                    request_id="request-independent",
                    kind="FILE_WRITE",
                    target_ref="file:independent.txt",
                    status=EffectStatus.COMMITTED,
                    checkpoint_id="checkpoint-independent",
                    created_at=datetime.now(UTC),
                ),
            )

    class RollbackExecutor:
        def __init__(self) -> None:
            self.plan: RollbackPlan | None = None

        async def execute_rollback_plan(self, plan: RollbackPlan) -> RollbackPlanResult:
            self.plan = plan
            return RollbackPlanResult(
                plan_id=plan.plan_id,
                task_id=plan.task_id,
                rolled_back_request_ids=plan.request_ids,
                failed_request_ids=[],
                status=ExecutionStatus.SUCCESS,
                reason="rolled back affected branch",
            )

    rollback = RollbackExecutor()
    graph = TaskGraph(
        graph_id="graph-v2",
        task_id="task-graph-v2",
        max_parallelism=1,
        nodes=[
            _node("failed", effect_targets=["file:failed.txt"]),
            _node(
                "independent",
                tool_name="write_file",
                arguments={"path": "independent.txt", "content": "independent"},
                effect_targets=["file:independent.txt"],
            ),
        ],
    )

    result = asyncio.run(
        RuntimeTaskGraphScheduler(
            runtime_scheduler=Scheduler(),
            effect_store=EffectStore(),
            rollback_executor=rollback,
        ).schedule_graph(graph)
    )

    assert rollback.plan is not None
    assert rollback.plan.request_ids == ["request-failed"]
    assert rollback.plan.effect_ids == ["effect-failed"]
    assert result.node_results["failed"].status is ExecutionStatus.ROLLED_BACK
    assert result.node_results["independent"].status is ExecutionStatus.COMMITTED


def test_failure_blocks_later_node_with_the_same_effect_target() -> None:
    class Scheduler:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
            self.calls.append(request.step_id)
            return ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=ExecutionStatus.FAILED,
                error="controlled failure",
            )

    runtime = Scheduler()
    graph = TaskGraph(
        graph_id="graph-v2",
        task_id="task-graph-v2",
        max_parallelism=1,
        nodes=[
            _node("a-failed", effect_targets=["file:shared.txt"]),
            _node("z-conflict", effect_targets=["file:shared.txt"]),
        ],
    )

    result = asyncio.run(RuntimeTaskGraphScheduler(runtime_scheduler=runtime).schedule_graph(graph))

    assert runtime.calls == ["a-failed"]
    assert result.blocked_nodes["z-conflict"] == "effect_target_failed"


def test_graph_cancellation_stops_running_node_and_never_starts_next_node() -> None:
    class Scheduler:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()
            self.calls: list[str] = []

        async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
            self.calls.append(request.step_id)
            self.started.set()
            await self.cancelled.wait()
            return ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=ExecutionStatus.CANCELLED,
                error="graph cancelled",
            )

        async def cancel_task(self, task_id: str) -> None:
            assert task_id == "task-graph-v2"
            self.cancelled.set()

    async def run() -> tuple[TaskGraphResult, Scheduler]:
        runtime = Scheduler()
        scheduler = RuntimeTaskGraphScheduler(runtime_scheduler=runtime)
        graph = TaskGraph(
            graph_id="graph-v2",
            task_id="task-graph-v2",
            max_parallelism=1,
            nodes=[_node("running"), _node("never", dependencies=["running"])],
        )
        task = asyncio.create_task(scheduler.schedule_graph(graph))
        await runtime.started.wait()
        await scheduler.cancel_graph("graph-v2")
        return await task, runtime

    result, runtime = asyncio.run(run())

    assert runtime.calls == ["running"]
    assert result.node_results["running"].status is ExecutionStatus.CANCELLED
    assert result.blocked_nodes["never"] == "graph_cancelled"


def test_graph_scheduler_records_reconstructable_graph_audit_without_tool_output() -> None:
    class Scheduler:
        async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
            return ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=ExecutionStatus.COMMITTED,
                output={"secret": "not an audit payload"},
            )

    recorder = InMemoryAuditRecorder()
    graph = TaskGraph(
        graph_id="graph-v2",
        task_id="task-graph-v2",
        max_parallelism=1,
        nodes=[_node("audit")],
    )

    asyncio.run(
        RuntimeTaskGraphScheduler(
            runtime_scheduler=Scheduler(), audit_recorder=recorder
        ).schedule_graph(graph)
    )

    events = recorder.events_for("task-graph-v2")
    assert [event.event_type.value for event in events] == [
        "PLAN_CREATED",
        "TOOL_REQUESTED",
        "EXECUTION_FINISHED",
        "TASK_FINISHED",
    ]
    assert all(event.details == {"graph_id": "graph-v2"} for event in events)
