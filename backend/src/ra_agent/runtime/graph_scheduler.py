"""Contract-v0.4 task-graph scheduling over the existing RuntimeScheduler."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Protocol, cast
from urllib.parse import urlsplit, urlunsplit

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
    running: set[str] = field(default_factory=set)
    cancelled: bool = False
    finished_at: datetime | None = None
    finished_audited: bool = False
    serialized_audited: set[str] = field(default_factory=set)


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

    async def resume_after_approval(
        self, graph_id: str, approval_id: str
    ) -> TaskGraphResult:
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
        await self._record(state, AuditEventType.TASK_CANCELLED, "CANCELLED", "graph cancelled")
        await self._rollback_cancelled_pending_effects(state)
        return await self._result_with_audit(state)

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
            rollback = await self._rollback_executor.execute_rollback_plan(plan)
            await self._record(
                state,
                AuditEventType.ROLLBACK_FINISHED,
                "ROLLED_BACK" if rollback.status is ExecutionStatus.SUCCESS else "FAILED",
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
                results = await asyncio.gather(
                    *(self._run_node(state, node) for node in batch)
                )
            finally:
                state.running.difference_update(node.node_id for node in batch)
            for node, result in zip(batch, results, strict=True):
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
        waiting_targets = set().union(
            *(self._node_targets(nodes[node_id]) for node_id in state.waiting)
        )
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
        occupied_targets = set(waiting_targets)
        for node in sorted(ready, key=lambda item: item.node_id):
            targets = self._node_targets(node)
            if targets & occupied_targets:
                continue
            if not self._is_parallel_safe(node) and batch:
                continue
            if batch and any(not self._is_parallel_safe(item) for item in batch):
                continue
            batch.append(node)
            occupied_targets.update(targets)
            if len(batch) == state.graph.max_parallelism:
                break
        return batch

    def _conflict_serialized_nodes(
        self,
        state: _GraphState,
        nodes: dict[str, TaskNode],
        batch: list[TaskNode],
    ) -> list[TaskNode]:
        occupied_targets = set().union(
            *(self._node_targets(nodes[node_id]) for node_id in state.waiting),
            *(self._node_targets(node) for node in batch),
        )
        return [
            node
            for node in state.graph.nodes
            if node.node_id not in {item.node_id for item in batch}
            and node.node_id not in state.results
            and node.node_id not in state.failed_descendants
            and node.node_id not in state.cancelled_nodes
            and node.node_id not in state.waiting
            and all(dependency in state.results for dependency in node.dependencies)
            and bool(self._node_targets(node) & occupied_targets)
        ]

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
        failed_targets = set().union(
            *(self._node_targets(nodes[node_id]) for node_id in failed_nodes)
        )
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
                elif self._node_targets(node) & failed_targets:
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

    async def _rollback_failed_effects(self, state: _GraphState) -> None:
        if self._effect_store is None or self._rollback_executor is None:
            return
        failed_requests = {
            result.request_id
            for result in state.results.values()
            if result.status in self._FAILURE_STATUSES
            and result.request_id not in state.rollback_attempted
        }
        if not failed_requests:
            return
        effects = await self._effect_store.list_by_task_id(state.graph.task_id)
        selected = [
            effect
            for effect in effects
            if effect.request_id in failed_requests
            and effect.status in {EffectStatus.PENDING, EffectStatus.COMMITTED}
        ]
        state.rollback_attempted.update(failed_requests)
        if not selected:
            return
        plan = RollbackPlan(
            plan_id=new_id("rollback-plan"),
            task_id=state.graph.task_id,
            trigger="graph_node_failure",
            request_ids=sorted({effect.request_id for effect in selected}),
            checkpoint_ids=sorted(
                {
                    effect.checkpoint_id
                    for effect in selected
                    if effect.checkpoint_id is not None
                }
            ),
            effect_ids=sorted({effect.effect_id for effect in selected}),
            reason="graph node failed after producing a managed effect",
        )
        await self._record(
            state,
            AuditEventType.PLAN_CREATED,
            "ROLLBACK_PLANNED",
            "selective graph rollback planned",
            details={"rollback_plan_id": plan.plan_id, "effect_count": len(selected)},
        )
        await self._record(
            state,
            AuditEventType.ROLLBACK_STARTED,
            "ROLLING_BACK",
            "selective graph rollback started",
            details={"rollback_plan_id": plan.plan_id, "effect_count": len(selected)},
        )
        try:
            rollback = await self._rollback_executor.execute_rollback_plan(plan)
        except Exception as error:
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
            self._mark_rollback_failure(state, failed_requests, rollback.reason)
            await self._record(
                state,
                AuditEventType.ROLLBACK_FINISHED,
                "FAILED",
                "selective graph rollback failed",
                details={"rollback_plan_id": plan.plan_id},
            )
            return
        rolled_back = set(rollback.rolled_back_request_ids)
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
            elif self._node_targets(node) & set().union(
                *(self._node_targets(nodes[waiting_node_id]) for waiting_node_id in state.waiting)
            ):
                blocked[node.node_id] = "effect_target_waiting_approval"
        return TaskGraphResult(
            graph_id=state.graph.graph_id,
            task_id=state.graph.task_id,
            node_results=state.results,
            blocked_nodes=blocked,
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
            node_id
            for node_id, result in state.results.items()
            if result.request_id in request_ids
        }

    def _node_targets(self, node: TaskNode) -> set[str]:
        trusted_target = self._trusted_builtin_target(node)
        if trusted_target is not None:
            return {trusted_target}
        if node.effect_targets or self._has_declared_side_effect(node):
            return {"__untrusted_side_effect__"}
        return set()

    def _is_parallel_safe(self, node: TaskNode) -> bool:
        return node.parallel_safe and self._node_targets(node) != {
            "__untrusted_side_effect__"
        }

    @staticmethod
    def _trusted_builtin_target(node: TaskNode) -> str | None:
        if node.request.tool_name in {"write_file", "delete_file"}:
            path = node.request.arguments.get("path")
            normalized_path = (
                RuntimeTaskGraphScheduler._normalized_safe_relative_path(path)
                if isinstance(path, str)
                else None
            )
            if normalized_path is None:
                return None
            return f"file:{normalized_path}"
        if node.request.tool_name == "memory_write":
            key = node.request.arguments.get("key")
            if (
                not isinstance(key, str)
                or not key
                or key != key.strip()
                or any(ord(character) < 32 for character in key)
            ):
                return None
            return f"memory:{key}"
        if node.request.tool_name == "download_url":
            url = node.request.arguments.get("url")
            if not isinstance(url, str):
                return None
            try:
                parsed = urlsplit(url)
                port = parsed.port
            except ValueError:
                return None
            if (
                parsed.scheme.casefold() not in {"http", "https"}
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
            ):
                return None
            host = parsed.hostname.casefold()
            netloc = f"[{host}]" if ":" in host else host
            if port is not None:
                netloc = f"{netloc}:{port}"
            normalized = urlunsplit(
                (parsed.scheme.casefold(), netloc, parsed.path or "/", parsed.query, "")
            )
            return f"download:{normalized}"
        return None

    @staticmethod
    def _normalized_safe_relative_path(path: str) -> str | None:
        if not path or "\\" in path or path.startswith("/") or ":" in path:
            return None
        candidate = PurePosixPath(path)
        if any(part in {"", ".", ".."} for part in candidate.parts):
            return None
        return candidate.as_posix()

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
