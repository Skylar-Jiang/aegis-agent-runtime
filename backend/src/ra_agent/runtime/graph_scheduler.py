"""Contract-v0.4 task-graph scheduling over the existing RuntimeScheduler."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, cast

from ra_agent.audit import AuditRecorder
from ra_agent.contracts import (
    AuditEventType,
    EffectRecord,
    EffectStatus,
    ExecutionStatus,
    RollbackPlan,
    RollbackPlanResult,
    TaskGraph,
    TaskGraphResult,
    TaskNode,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.core.ids import new_id
from ra_agent.execution.recovery import DependencyRecoveryPlanner

from .effect_targets import (
    EffectOperation,
    EffectScope,
    EffectTarget,
    conflict_reason,
    infer_effect_targets,
)


class _ToolScheduler(Protocol):
    async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult: ...


class _ApprovalResumer(Protocol):
    async def resume_after_approval(
        self, request: ToolCallRequest, approval_id: str
    ) -> ToolExecutionResult: ...


class _TaskCanceller(Protocol):
    async def cancel_task(self, task_id: str) -> None: ...


class _EffectStore(Protocol):
    async def list_by_task_id(self, task_id: str) -> tuple[EffectRecord, ...]: ...


class _RollbackExecutor(Protocol):
    async def execute_rollback_plan(self, plan: RollbackPlan) -> RollbackPlanResult: ...


@dataclass(slots=True)
class _GraphState:
    graph: TaskGraph
    started_at: datetime
    results: dict[str, ToolExecutionResult] = field(default_factory=dict)
    failed_descendants: dict[str, str] = field(default_factory=dict)
    cancelled_nodes: dict[str, str] = field(default_factory=dict)
    waiting: dict[str, str] = field(default_factory=dict)
    rollback_attempted: set[str] = field(default_factory=set)
    rollback_succeeded: set[str] = field(default_factory=set)
    rollback_failures: dict[str, str] = field(default_factory=dict)
    recovery_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    running: set[str] = field(default_factory=set)
    cancelled: bool = False
    cancellation_complete: asyncio.Event = field(default_factory=asyncio.Event)
    finished_at: datetime | None = None
    finished_audited: bool = False
    serialized_audited: set[str] = field(default_factory=set)
    inferred_conflicts: dict[str, dict[str, str]] = field(default_factory=dict)


class RuntimeTaskGraphScheduler:
    """Run ready graph nodes through RuntimeScheduler, never a ToolExecutor.

    Approval resume state is intentionally in-process only for V2 P0. A process
    restart keeps the durable approval decision but cannot resume its graph until a
    later durable graph-state Contract is introduced.
    """

    _FAILURE_STATUSES = frozenset(
        {
            ExecutionStatus.FAILED,
            ExecutionStatus.BLOCKED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.TIMEOUT,
            ExecutionStatus.ROLLED_BACK,
        }
    )

    def __init__(
        self,
        *,
        runtime_scheduler: object,
        effect_store: _EffectStore | None = None,
        rollback_executor: _RollbackExecutor | None = None,
        audit_recorder: AuditRecorder | None = None,
        pause_on_waiting_approval: bool = False,
    ) -> None:
        self._runtime_scheduler = runtime_scheduler
        self._effect_store = effect_store
        self._rollback_executor = rollback_executor
        self._audit_recorder = audit_recorder
        self._pause_on_waiting_approval = pause_on_waiting_approval
        self._states: dict[str, _GraphState] = {}

    async def schedule_graph(self, graph: TaskGraph) -> TaskGraphResult:
        await self.prepare_graph(graph)
        state = self._states[graph.graph_id]
        await self._run_ready_nodes(state)
        return await self._result_with_audit(state)

    def recovery_failures(self, graph_id: str) -> dict[str, str]:
        """Return unresolved rollback failures for an in-process graph."""
        return dict(self._states[graph_id].rollback_failures)

    async def retry_failed_recovery(self, graph_id: str) -> TaskGraphResult:
        """Retry only after an explicit caller request and durable status reconciliation."""
        state = self._states[graph_id]
        async with state.recovery_lock:
            return await self._retry_failed_recovery_locked(state)

    async def _retry_failed_recovery_locked(self, state: _GraphState) -> TaskGraphResult:
        if state.running:
            raise RuntimeError("cannot retry recovery while graph nodes are running")
        if state.cancelled and not state.cancellation_complete.is_set():
            raise RuntimeError("cannot retry recovery while graph cancellation is running")
        if self._effect_store is None:
            raise RuntimeError("effect store is required for recovery retry")
        failed = set(state.rollback_failures)
        if not failed:
            return await self._result_with_audit(state)
        effects = await self._effect_store.list_by_task_id(state.graph.task_id)
        by_request: dict[str, EffectRecord] = {}
        for effect in effects:
            if effect.task_id != state.graph.task_id:
                raise RuntimeError("cannot reconcile effect belonging to another task")
            if effect.request_id in by_request:
                raise RuntimeError(f"multiple effects share request id: {effect.request_id}")
            by_request[effect.request_id] = effect
        for request_id in failed:
            effect = by_request.get(request_id)
            if effect is None:
                raise RuntimeError(f"cannot reconcile missing effect: {request_id}")
            if effect.status not in {
                EffectStatus.PENDING,
                EffectStatus.COMMITTED,
                EffectStatus.ROLLED_BACK,
            }:
                raise RuntimeError(f"cannot safely retry effect in {effect.status} state")
            if effect.status is EffectStatus.ROLLED_BACK:
                state.rollback_succeeded.add(request_id)
                state.rollback_failures.pop(request_id, None)
                for node_id, result in tuple(state.results.items()):
                    if result.request_id == request_id:
                        state.results[node_id] = result.model_copy(
                            update={"status": ExecutionStatus.ROLLED_BACK}
                        )
            else:
                state.rollback_attempted.discard(request_id)
        await self._rollback_failed_effects(state, retry_request_ids=failed, lock_held=True)
        return await self._result_with_audit(state)

    async def prepare_graph(self, graph: TaskGraph) -> TaskGraphResult:
        """Register an in-process graph before its background execution begins."""

        state = self._states.get(graph.graph_id)
        if state is None:
            state = _GraphState(graph=graph, started_at=datetime.now(UTC))
            self._states[graph.graph_id] = state
            await self._record(
                state,
                AuditEventType.PLAN_CREATED,
                "RUNNING",
                "task graph scheduled",
            )
        elif state.graph != graph:
            raise ValueError("graph_id already belongs to a different TaskGraph")
        return self._result(state)

    async def snapshot(self, graph_id: str) -> TaskGraphResult:
        """Return the in-process graph state without exposing tool output."""

        state = self._states.get(graph_id)
        if state is None:
            raise KeyError(f"unknown graph: {graph_id}")
        return self._result(state)

    async def resume_after_approval(self, graph_id: str, approval_id: str) -> TaskGraphResult:
        state = self._states.get(graph_id)
        if state is None:
            raise KeyError(f"unknown graph: {graph_id}")
        if state.cancelled:
            raise RuntimeError("graph is cancelled and cannot resume")
        matching_nodes = [
            node_id
            for node_id, pending_approval_id in state.waiting.items()
            if pending_approval_id == approval_id
        ]
        if len(matching_nodes) != 1:
            raise ValueError("approval does not match exactly one waiting graph node")
        node_id = matching_nodes[0]
        node = self._nodes(state)[node_id]
        result = await self._resume_node(node, approval_id)
        if result.status is ExecutionStatus.WAITING_APPROVAL:
            state.waiting[node_id] = approval_id
        else:
            state.waiting.pop(node_id)
            state.results[node_id] = result
            await self._record_result(state, node, result)
            await self._rollback_failed_effects(state)
            await self._run_ready_nodes(state)
        return await self._result_with_audit(state)

    async def cancel_graph(self, graph_id: str) -> TaskGraphResult:
        state = self._states.get(graph_id)
        if state is None:
            raise KeyError(f"unknown graph: {graph_id}")
        state.cancelled = True
        try:
            for node_id in tuple(state.waiting):
                state.waiting.pop(node_id)
                state.cancelled_nodes[node_id] = "graph_cancelled"
            for node in state.graph.nodes:
                if (
                    node.node_id not in state.results
                    and node.node_id not in state.running
                    and node.node_id not in state.waiting
                ):
                    state.cancelled_nodes[node.node_id] = "graph_cancelled"
            if not callable(getattr(self._runtime_scheduler, "cancel_task", None)):
                raise RuntimeError("runtime scheduler does not support graph cancellation")
            await cast(_TaskCanceller, self._runtime_scheduler).cancel_task(state.graph.task_id)
            await self._record(
                state,
                AuditEventType.TASK_CANCELLED,
                "CANCELLED",
                "graph cancelled",
            )
            async with state.recovery_lock:
                await self._rollback_cancelled_pending_effects(state)
            return await self._result_with_audit(state)
        finally:
            state.cancellation_complete.set()

    async def _rollback_cancelled_pending_effects(self, state: _GraphState) -> None:
        if self._effect_store is None or self._rollback_executor is None:
            return
        effects = await self._effect_store.list_by_task_id(state.graph.task_id)
        pending = [effect for effect in effects if effect.status is EffectStatus.PENDING]
        if pending:
            plan = RollbackPlan(
                plan_id=new_id("rollback-plan"),
                task_id=state.graph.task_id,
                trigger="graph_cancelled",
                request_ids=sorted(effect.request_id for effect in pending),
                effect_ids=sorted(effect.effect_id for effect in pending),
                reason="graph cancelled before pending effects could commit",
            )
            await self._record(
                state,
                AuditEventType.ROLLBACK_STARTED,
                "ROLLING_BACK",
                "selective graph rollback started after cancellation",
                details={"rollback_plan_id": plan.plan_id, "effect_count": len(pending)},
            )
            state.rollback_attempted.update(plan.request_ids)
            try:
                rollback = await self._rollback_executor.execute_rollback_plan(plan)
            except Exception as error:
                state.rollback_failures.update(
                    {request_id: type(error).__name__ for request_id in plan.request_ids}
                )
                await self._record(
                    state,
                    AuditEventType.ROLLBACK_FINISHED,
                    "FAILED",
                    "selective graph rollback failed after cancellation",
                    details={"rollback_plan_id": plan.plan_id},
                )
                return
            rolled_back = set(rollback.rolled_back_request_ids)
            state.rollback_succeeded.update(rolled_back)
            for node_id, result in tuple(state.results.items()):
                if result.request_id in rolled_back:
                    state.results[node_id] = result.model_copy(
                        update={"status": ExecutionStatus.ROLLED_BACK, "error": rollback.reason}
                    )
            if rollback.status is not ExecutionStatus.SUCCESS or rollback.failed_request_ids:
                failed = rollback.failed_request_ids or plan.request_ids
                state.rollback_failures.update(
                    {request_id: rollback.reason for request_id in failed}
                )
            await self._record(
                state,
                AuditEventType.ROLLBACK_FINISHED,
                "ROLLED_BACK"
                if rollback.status is ExecutionStatus.SUCCESS and not rollback.failed_request_ids
                else "FAILED",
                "selective graph rollback completed after cancellation",
                details={"rollback_plan_id": plan.plan_id, "effect_count": len(pending)},
            )
        for effect in effects:
            if effect.status is EffectStatus.COMMITTED:
                await self._record(
                    state,
                    AuditEventType.EXECUTION_FINISHED,
                    "PRESERVED",
                    "independent graph effect preserved during selective rollback",
                    details={"effect_id": effect.effect_id},
                )

    async def _run_ready_nodes(self, state: _GraphState) -> None:
        nodes = self._nodes(state)
        while True:
            self._block_failed_descendants(state, nodes)
            batch = self._select_batch(state, nodes)
            if not batch:
                return
            for node in self._conflict_serialized_nodes(state, nodes, batch):
                if node.node_id not in state.serialized_audited:
                    state.serialized_audited.add(node.node_id)
                    await self._record(
                        state,
                        AuditEventType.PLAN_CREATED,
                        "SERIALIZED",
                        "graph node serialized by effect conflict",
                        node=node,
                        details={
                            "inferred_effect_targets": [
                                target.render() for target in self._effect_targets(node)
                            ],
                            "conflicts": state.inferred_conflicts.get(node.node_id, {}),
                        },
                    )
            for node in batch:
                await self._record(
                    state,
                    AuditEventType.TOOL_REQUESTED,
                    "READY",
                    "graph node dispatched",
                    node=node,
                )
            state.running.update(node.node_id for node in batch)
            try:
                results = await asyncio.gather(*(self._run_node(state, node) for node in batch))
            finally:
                state.running.difference_update(node.node_id for node in batch)
            if state.cancelled:
                await state.cancellation_complete.wait()
            if state.cancelled and self._effect_store is not None:
                final_effects = await self._effect_store.list_by_task_id(state.graph.task_id)
                state.rollback_succeeded.update(
                    effect.request_id
                    for effect in final_effects
                    if effect.status is EffectStatus.ROLLED_BACK
                )
            for node, result in zip(batch, results, strict=True):
                if state.cancelled and result.request_id in state.rollback_succeeded:
                    result = result.model_copy(
                        update={
                            "status": ExecutionStatus.ROLLED_BACK,
                            "error": "graph cancelled; pending effect rolled back",
                        }
                    )
                if result.status is ExecutionStatus.WAITING_APPROVAL:
                    approval_id = self._approval_id(result)
                    if approval_id is None:
                        state.results[node.node_id] = self._failed_result(
                            node.request,
                            "approval request did not return a valid approval_id",
                        )
                    else:
                        state.waiting[node.node_id] = approval_id
                        await self._record(
                            state,
                            AuditEventType.APPROVAL_REQUESTED,
                            "WAITING_APPROVAL",
                            "graph node waiting for approval",
                            node=node,
                        )
                else:
                    state.results[node.node_id] = result
                    await self._record_result(state, node, result)
            await self._persist_dependency_lineage(state, nodes)
            await self._rollback_failed_effects(state)

    def _select_batch(
        self,
        state: _GraphState,
        nodes: dict[str, TaskNode],
    ) -> list[TaskNode]:
        if state.cancelled:
            return []
        if self._pause_on_waiting_approval and state.waiting:
            return []
        occupied = [nodes[node_id] for node_id in state.waiting]
        ready = [
            node
            for node in state.graph.nodes
            if node.node_id not in state.results
            and node.node_id not in state.failed_descendants
            and node.node_id not in state.cancelled_nodes
            and node.node_id not in state.waiting
            and all(dependency in state.results for dependency in node.dependencies)
        ]
        batch: list[TaskNode] = []
        for node in sorted(ready, key=lambda item: item.node_id):
            reason, conflicting_node = self._first_conflict(node, occupied)
            if reason is not None and conflicting_node is not None:
                state.inferred_conflicts[node.node_id] = {
                    "reason": reason,
                    "conflicting_node_id": conflicting_node.node_id,
                }
                continue
            if not self._is_parallel_safe(node) and batch:
                continue
            if batch and any(not self._is_parallel_safe(item) for item in batch):
                continue
            batch.append(node)
            occupied.append(node)
            if len(batch) == state.graph.max_parallelism:
                break
        return batch

    def _conflict_serialized_nodes(
        self,
        state: _GraphState,
        nodes: dict[str, TaskNode],
        batch: list[TaskNode],
    ) -> list[TaskNode]:
        occupied = [nodes[node_id] for node_id in state.waiting] + batch
        serialized: list[TaskNode] = []
        for node in state.graph.nodes:
            if (
                node.node_id in {item.node_id for item in batch}
                or node.node_id in state.results
                or node.node_id in state.failed_descendants
                or node.node_id in state.cancelled_nodes
                or node.node_id in state.waiting
                or not all(dependency in state.results for dependency in node.dependencies)
            ):
                continue
            reason, conflicting_node = self._first_conflict(node, occupied)
            if reason is not None and conflicting_node is not None:
                state.inferred_conflicts[node.node_id] = {
                    "reason": reason,
                    "conflicting_node_id": conflicting_node.node_id,
                }
                serialized.append(node)
        return serialized

    def _block_failed_descendants(
        self,
        state: _GraphState,
        nodes: dict[str, TaskNode],
    ) -> None:
        failed_nodes = {
            node_id
            for node_id, result in state.results.items()
            if result.status in self._FAILURE_STATUSES
        }
        failed_nodes_by_id = [nodes[node_id] for node_id in failed_nodes]
        blocked_nodes = failed_nodes | set(state.failed_descendants)
        changed = True
        while changed:
            changed = False
            for node_id, node in nodes.items():
                if (
                    node_id in state.results
                    or node_id in state.waiting
                    or node_id in state.cancelled_nodes
                    or node_id in state.failed_descendants
                ):
                    continue
                if any(dependency in blocked_nodes for dependency in node.dependencies):
                    state.failed_descendants[node_id] = "dependency_failed"
                elif self._first_conflict(node, failed_nodes_by_id)[0] is not None:
                    state.failed_descendants[node_id] = "effect_target_failed"
                else:
                    continue
                blocked_nodes.add(node_id)
                changed = True

    async def _run_node(
        self,
        state: _GraphState,
        node: TaskNode,
    ) -> ToolExecutionResult:
        try:
            return await cast(_ToolScheduler, self._runtime_scheduler).schedule(node.request)
        except asyncio.CancelledError:
            return ToolExecutionResult(
                task_id=node.request.task_id,
                step_id=node.request.step_id,
                request_id=node.request.request_id,
                status=ExecutionStatus.CANCELLED,
                error=(
                    "graph cancelled"
                    if state.cancelled
                    else "controlled runtime execution interrupted"
                ),
            )
        except Exception as error:
            return self._failed_result(node.request, str(error) or type(error).__name__)

    async def _resume_node(
        self,
        node: TaskNode,
        approval_id: str,
    ) -> ToolExecutionResult:
        try:
            if not callable(getattr(self._runtime_scheduler, "resume_after_approval", None)):
                raise RuntimeError("runtime scheduler does not support approval resume")
            return await cast(_ApprovalResumer, self._runtime_scheduler).resume_after_approval(
                node.request,
                approval_id,
            )
        except Exception as error:
            return self._failed_result(node.request, str(error) or type(error).__name__)

    async def _rollback_failed_effects(
        self,
        state: _GraphState,
        *,
        retry_request_ids: set[str] | None = None,
        lock_held: bool = False,
    ) -> None:
        if not lock_held:
            async with state.recovery_lock:
                await self._rollback_failed_effects(
                    state, retry_request_ids=retry_request_ids, lock_held=True
                )
            return
        if self._effect_store is None or self._rollback_executor is None:
            return
        failed_requests = retry_request_ids or {
            result.request_id
            for result in state.results.values()
            if result.status in self._FAILURE_STATUSES
            and result.request_id not in state.rollback_attempted
        }
        if not failed_requests:
            return
        attempted_this_call = set(failed_requests)
        state.rollback_attempted.update(failed_requests)
        try:
            await self._rollback_failed_effects_once(state, failed_requests, attempted_this_call)
        except asyncio.CancelledError:
            unresolved = attempted_this_call - state.rollback_succeeded
            state.rollback_failures.update(
                {request_id: "recovery cancelled" for request_id in unresolved}
            )
            self._mark_rollback_failure(state, unresolved, "recovery cancelled")
            raise
        except Exception as error:
            unresolved = attempted_this_call - state.rollback_succeeded
            state.rollback_failures.update(
                {request_id: type(error).__name__ for request_id in unresolved}
            )
            self._mark_rollback_failure(state, unresolved, type(error).__name__)

    async def _rollback_failed_effects_once(
        self,
        state: _GraphState,
        failed_requests: set[str],
        attempted_this_call: set[str],
    ) -> None:
        if self._effect_store is None or self._rollback_executor is None:
            return
        effects = await self._effect_store.list_by_task_id(state.graph.task_id)
        failed_effects = [effect for effect in effects if effect.request_id in failed_requests]
        if not failed_effects:
            return
        recovery = await DependencyRecoveryPlanner(self._effect_store).plan(
            task_id=state.graph.task_id,
            failed_effect_ids=sorted(effect.effect_id for effect in failed_effects),
            trigger="graph_node_failure",
        )
        if not recovery.rollback_effect_ids:
            return
        selected = [
            effect for effect in effects if effect.effect_id in set(recovery.rollback_effect_ids)
        ]
        plan = RollbackPlan(
            plan_id=recovery.rollback_plan_id,
            task_id=recovery.task_id,
            trigger=recovery.trigger,
            request_ids=sorted({effect.request_id for effect in selected}),
            checkpoint_ids=sorted(
                {effect.checkpoint_id for effect in selected if effect.checkpoint_id is not None}
            ),
            effect_ids=recovery.rollback_effect_ids,
            reason=recovery.reason,
        )
        state.rollback_attempted.update(plan.request_ids)
        attempted_this_call.update(plan.request_ids)
        await self._record(
            state,
            AuditEventType.PLAN_CREATED,
            "ROLLBACK_PLANNED",
            "selective graph rollback planned",
            details={
                "rollback_plan_id": plan.plan_id,
                "effect_count": len(recovery.rollback_effect_ids),
                "affected_effect_ids": recovery.affected_effect_ids,
                "preserve_effect_ids": recovery.preserve_effect_ids,
            },
        )
        await self._record(
            state,
            AuditEventType.ROLLBACK_STARTED,
            "ROLLING_BACK",
            "selective graph rollback started",
            details={
                "rollback_plan_id": plan.plan_id,
                "effect_count": len(recovery.rollback_effect_ids),
                "affected_effect_ids": recovery.affected_effect_ids,
            },
        )
        try:
            rollback = await self._rollback_executor.execute_rollback_plan(plan)
        except Exception as error:
            state.rollback_failures.update(
                {request_id: type(error).__name__ for request_id in plan.request_ids}
            )
            self._mark_rollback_failure(state, failed_requests, type(error).__name__)
            await self._record(
                state,
                AuditEventType.ROLLBACK_FINISHED,
                "FAILED",
                "selective graph rollback failed",
                details={"rollback_plan_id": plan.plan_id},
            )
            return
        if rollback.status is not ExecutionStatus.SUCCESS or rollback.failed_request_ids:
            rolled_back = set(rollback.rolled_back_request_ids)
            state.rollback_succeeded.update(rolled_back)
            for node_id, result in tuple(state.results.items()):
                if result.request_id in rolled_back:
                    state.results[node_id] = result.model_copy(
                        update={"status": ExecutionStatus.ROLLED_BACK, "error": rollback.reason}
                    )
            failed = rollback.failed_request_ids or plan.request_ids
            state.rollback_failures.update({request_id: rollback.reason for request_id in failed})
            self._mark_rollback_failure(state, set(failed), rollback.reason)
            await self._record(
                state,
                AuditEventType.ROLLBACK_FINISHED,
                "FAILED",
                "selective graph rollback failed",
                details={"rollback_plan_id": plan.plan_id},
            )
            return
        rolled_back = set(rollback.rolled_back_request_ids)
        state.rollback_succeeded.update(rolled_back)
        for request_id in failed_requests:
            state.rollback_failures.pop(request_id, None)
        for node_id, result in tuple(state.results.items()):
            if result.request_id in rolled_back:
                state.results[node_id] = result.model_copy(
                    update={
                        "status": ExecutionStatus.ROLLED_BACK,
                        "error": rollback.reason,
                    }
                )
        await self._record(
            state,
            AuditEventType.ROLLBACK_FINISHED,
            "ROLLED_BACK",
            "selective graph rollback completed",
            details={"rollback_plan_id": plan.plan_id, "effect_count": len(rolled_back)},
        )
        for node_id, result in state.results.items():
            if (
                node_id not in self._nodes_for_request_ids(state, rolled_back)
                and result.status is ExecutionStatus.COMMITTED
            ):
                await self._record(
                    state,
                    AuditEventType.EXECUTION_FINISHED,
                    "PRESERVED",
                    "independent graph node preserved during selective rollback",
                    node=self._nodes(state)[node_id],
                )

    async def _persist_dependency_lineage(
        self,
        state: _GraphState,
        nodes: dict[str, TaskNode],
    ) -> None:
        """Record explicit task dependencies as durable child-effect edges.

        This is deliberately best-effort only when an effect store exposes the
        lineage API: read-only nodes and legacy stores have no managed effect.
        """

        if self._effect_store is None:
            return
        get_by_request_id = cast(
            Callable[[str], Awaitable[EffectRecord | None]] | None,
            getattr(self._effect_store, "get_by_request_id", None),
        )
        attach_parent_effect_ids = cast(
            Callable[[str, list[str]], Awaitable[EffectRecord]] | None,
            getattr(self._effect_store, "attach_parent_effect_ids", None),
        )
        if not callable(get_by_request_id) or not callable(attach_parent_effect_ids):
            return
        for node_id, result in state.results.items():
            node = nodes[node_id]
            if not node.dependencies or result.status is not ExecutionStatus.COMMITTED:
                continue
            child = await get_by_request_id(result.request_id)
            if child is None:
                continue
            parent_effect_ids: list[str] = []
            for parent_node_id in node.dependencies:
                parent_result = state.results.get(parent_node_id)
                if parent_result is None or parent_result.status is not ExecutionStatus.COMMITTED:
                    continue
                parent = await get_by_request_id(parent_result.request_id)
                if parent is not None:
                    parent_effect_ids.append(parent.effect_id)
            if parent_effect_ids:
                await attach_parent_effect_ids(child.effect_id, parent_effect_ids)

    @staticmethod
    def _mark_rollback_failure(
        state: _GraphState,
        request_ids: set[str],
        reason: str,
    ) -> None:
        for node_id, result in tuple(state.results.items()):
            if result.request_id in request_ids:
                state.results[node_id] = result.model_copy(
                    update={"error": f"{result.error or 'node failed'}; rollback failed: {reason}"}
                )

    def _result(self, state: _GraphState) -> TaskGraphResult:
        nodes = self._nodes(state)
        blocked = dict(state.failed_descendants)
        blocked.update(state.cancelled_nodes)
        blocked.update({node_id: "WAITING_APPROVAL" for node_id in state.waiting})
        for node in state.graph.nodes:
            if node.node_id in state.results or node.node_id in blocked:
                continue
            if any(dependency in state.waiting for dependency in node.dependencies):
                blocked[node.node_id] = "dependency_waiting_approval"
            elif (
                self._first_conflict(
                    node, [nodes[waiting_node_id] for waiting_node_id in state.waiting]
                )[0]
                is not None
            ):
                blocked[node.node_id] = "effect_target_waiting_approval"
        return TaskGraphResult(
            graph_id=state.graph.graph_id,
            task_id=state.graph.task_id,
            node_results=state.results,
            blocked_nodes=blocked,
            inferred_effect_targets={
                node.node_id: [target.render() for target in self._effect_targets(node)]
                for node in state.graph.nodes
            },
            inferred_conflicts=dict(state.inferred_conflicts),
            started_at=state.started_at,
            finished_at=self._finished_at(state),
        )

    async def _result_with_audit(self, state: _GraphState) -> TaskGraphResult:
        result = self._result(state)
        if self._is_terminal(state) and not state.finished_audited:
            state.finished_audited = True
            await self._record(
                state,
                AuditEventType.TASK_FINISHED,
                "COMPLETED" if not result.blocked_nodes else "FAILED",
                "task graph scheduling finished",
            )
        return result

    async def _record_result(
        self,
        state: _GraphState,
        node: TaskNode,
        result: ToolExecutionResult,
    ) -> None:
        event_type = (
            AuditEventType.STEP_FAILED
            if result.status in self._FAILURE_STATUSES
            else AuditEventType.EXECUTION_FINISHED
        )
        await self._record(
            state,
            event_type,
            result.status.value,
            "graph node completed",
            node=node,
        )

    async def _record(
        self,
        state: _GraphState,
        event_type: AuditEventType,
        status: str,
        summary: str,
        *,
        node: TaskNode | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        if self._audit_recorder is None:
            return
        payload: dict[str, object] = {"graph_id": state.graph.graph_id}
        if details:
            payload.update(details)
        await self._audit_recorder.record(
            task_id=state.graph.task_id,
            step_id=node.request.step_id if node else None,
            request_id=node.request.request_id if node else None,
            event_type=event_type,
            actor="task-graph-scheduler",
            status=status,
            summary=summary,
            details=payload,
        )

    @staticmethod
    def _nodes(state: _GraphState) -> dict[str, TaskNode]:
        return {node.node_id: node for node in state.graph.nodes}

    @staticmethod
    def _approval_id(result: ToolExecutionResult) -> str | None:
        output = result.output
        if not isinstance(output, dict):
            return None
        approval_id = output.get("approval_id")
        return approval_id if isinstance(approval_id, str) and approval_id else None

    @staticmethod
    def _nodes_for_request_ids(state: _GraphState, request_ids: set[str]) -> set[str]:
        return {
            node_id for node_id, result in state.results.items() if result.request_id in request_ids
        }

    def _effect_targets(self, node: TaskNode) -> tuple[EffectTarget, ...]:
        """Infer builtin targets, retaining old hints only as extra constraints."""

        inferred = infer_effect_targets(node.request)
        if inferred[0].resource_type == "untrusted":
            return inferred
        advisory = tuple(
            EffectTarget("declared", value, EffectOperation.WRITE, EffectScope.EXACT)
            for value in sorted(set(node.effect_targets))
            if value
        )
        return inferred + advisory

    def _node_targets(self, node: TaskNode) -> set[str]:
        """Legacy rendered view retained for compatibility with internal callers."""

        inferred = infer_effect_targets(node.request)
        if inferred[0].resource_type == "untrusted":
            return {"__untrusted_side_effect__"}
        return {f"{target.resource_type}:{target.canonical_target}" for target in inferred}

    def _first_conflict(
        self,
        node: TaskNode,
        occupied: list[TaskNode],
    ) -> tuple[str | None, TaskNode | None]:
        for other in occupied:
            for target in self._effect_targets(node):
                for other_target in self._effect_targets(other):
                    reason = conflict_reason(target, other_target)
                    if reason is not None:
                        return reason, other
        return None, None

    def _is_parallel_safe(self, node: TaskNode) -> bool:
        return node.parallel_safe and all(
            target.resource_type != "untrusted" for target in self._effect_targets(node)
        )

    def _is_terminal(self, state: _GraphState) -> bool:
        covered_nodes = (
            set(state.results) | set(state.failed_descendants) | set(state.cancelled_nodes)
        )
        return (
            not state.waiting
            and not state.running
            and covered_nodes == {node.node_id for node in state.graph.nodes}
        )

    def _finished_at(self, state: _GraphState) -> datetime | None:
        if self._is_terminal(state) and state.finished_at is None:
            state.finished_at = datetime.now(UTC)
        return state.finished_at

    def _has_declared_side_effect(self, node: TaskNode) -> bool:
        registry = getattr(self._runtime_scheduler, "tool_registry", None)
        if registry is None:
            return False
        try:
            side_effect_type = registry.get_spec(node.request.tool_name).side_effect_type
        except (KeyError, AttributeError):
            return True
        return side_effect_type.upper() not in {"NONE", "READ"}

    @staticmethod
    def _failed_result(request: ToolCallRequest, reason: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.FAILED,
            error=reason,
        )
