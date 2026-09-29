import json
from collections.abc import Awaitable, Callable
from typing import Protocol, cast

from ra_agent.contracts import ExecutionStatus, TaskContract, ToolCallRequest, ToolExecutionResult
from ra_agent.runtime.correlation import (
    CorrelationError,
    validate_agent_request,
    validate_agent_result,
)

from .planner import IterativePlanner, Planner, PlanningError
from .state import AgentRunStatus, AgentState


class RuntimeSchedulerPort(Protocol):
    async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult: ...

    async def resume_after_approval(
        self, request: ToolCallRequest, approval_id: str
    ) -> ToolExecutionResult: ...


class AgentRuntime:
    """Minimal Agent state flow; all tool requests go through the Runtime scheduler."""

    def __init__(
        self,
        *,
        planner: Planner | IterativePlanner,
        scheduler: RuntimeSchedulerPort,
        max_turns: int = 8,
    ) -> None:
        if max_turns <= 0:
            raise ValueError("max_turns must be positive")
        self.planner = planner
        self.scheduler = scheduler
        self.max_turns = max_turns

    async def run(
        self,
        task_id: str,
        objective: str,
        contract: TaskContract | None = None,
        context_messages: list[dict[str, str]] | None = None,
        on_state_change: Callable[[AgentState], Awaitable[None]] | None = None,
    ) -> AgentState:
        planning_context = list(context_messages or [])
        if contract is not None:
            planning_context.append(
                {
                    "role": "system",
                    "content": "Effective Runtime TaskContract: "
                    + json.dumps(
                        {
                            "allowed_actions": contract.allowed_actions,
                            "allowed_resources": contract.allowed_resources,
                            "allow_egress": contract.allow_egress,
                            "security_profile_id": contract.security_profile_id,
                            "security_profile_version": contract.security_profile_version,
                        },
                        ensure_ascii=False,
                    ),
                }
            )
        state = AgentState(
            task_id=task_id,
            objective=objective,
            context_messages=planning_context,
        )
        if isinstance(self.planner, IterativePlanner):
            return await self._run_iteratively(state, contract, on_state_change)
        try:
            state.planned_requests = [
                request.model_copy(update={"task_contract": contract or request.task_contract})
                for request in await self.planner.plan(task_id, objective)
            ]
        except Exception as error:
            self._planner_failed(state, error)
            return state
        state.status = AgentRunStatus.RUNNING
        await self._notify(state, on_state_change)
        return await self._run_planned(state, start_index=0, on_state_change=on_state_change)

    async def resume_after_approval(
        self,
        state: AgentState,
        approval_id: str,
        contract: TaskContract | None = None,
        on_state_change: Callable[[AgentState], Awaitable[None]] | None = None,
    ) -> AgentState:
        """Resume one paused Agent task, then continue its remaining planning turns."""

        if state.status is not AgentRunStatus.WAITING_APPROVAL:
            raise ValueError("Agent task is not waiting for approval")
        if not state.planned_requests or not state.results:
            raise ValueError("Agent task has no resumable tool request")
        waiting = state.results[-1]
        request_index = next(
            (
                index
                for index, planned in enumerate(state.planned_requests)
                if planned.request_id == waiting.request_id
            ),
            None,
        )
        if request_index is None or waiting.status is not ExecutionStatus.WAITING_APPROVAL:
            raise ValueError("Agent task approval state is inconsistent")
        request = state.planned_requests[request_index]

        result = await self.scheduler.resume_after_approval(request, approval_id)
        try:
            validate_agent_result(request, result)
        except CorrelationError as error:
            result = ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=ExecutionStatus.FAILED,
                error=str(error),
                error_code=error.error_code,
            )
        state.results[-1] = result
        should_continue = self._apply_result_status(state, result)
        await self._notify(state, on_state_change)
        if not should_continue:
            return state

        state.status = AgentRunStatus.RUNNING
        if isinstance(self.planner, IterativePlanner):
            remaining_turns = self.max_turns - len(state.planned_requests)
            return await self._continue_iteratively(
                state, contract, remaining_turns, on_state_change=on_state_change
            )
        return await self._run_planned(
            state,
            start_index=request_index + 1,
            on_state_change=on_state_change,
        )

    async def cancel_task(self, task_id: str) -> None:
        cancel = getattr(self.scheduler, "cancel_task", None)
        if cancel is not None:
            await cancel(task_id)

    async def _run_iteratively(
        self,
        state: AgentState,
        contract: TaskContract | None,
        on_state_change: Callable[[AgentState], Awaitable[None]] | None,
    ) -> AgentState:
        planner = cast(IterativePlanner, self.planner)
        state.status = AgentRunStatus.RUNNING
        await self._notify(state, on_state_change)
        return await self._continue_iteratively(
            state,
            contract,
            self.max_turns,
            planner,
            on_state_change,
        )

    async def _continue_iteratively(
        self,
        state: AgentState,
        contract: TaskContract | None,
        remaining_turns: int,
        planner: IterativePlanner | None = None,
        on_state_change: Callable[[AgentState], Awaitable[None]] | None = None,
    ) -> AgentState:
        planner = planner or cast(IterativePlanner, self.planner)
        for _ in range(max(0, remaining_turns)):
            try:
                contextual = getattr(planner, "next_action_with_context", None)
                if contextual is not None and state.context_messages:
                    decision = await contextual(
                        state.task_id,
                        state.objective,
                        state.results,
                        state.context_messages,
                    )
                else:
                    decision = await planner.next_action(
                        state.task_id, state.objective, state.results
                    )
            except Exception as error:
                self._planner_failed(state, error)
                return state
            if decision.final_answer is not None:
                state.final_answer = decision.final_answer
                state.status = AgentRunStatus.COMPLETED
                return state
            request = decision.tool_call
            if request is None:
                state.status = AgentRunStatus.FAILED
                return state
            request = request.model_copy(
                update={"task_contract": contract or request.task_contract}
            )
            state.planned_requests.append(request)
            await self._notify(state, on_state_change)
            if not await self._schedule_request(state, request):
                await self._notify(state, on_state_change)
                return state
            await self._notify(state, on_state_change)
        state.status = AgentRunStatus.FAILED
        state.failure_code = "MAX_TURNS_EXCEEDED"
        state.failure_reason = "Agent reached the configured planning turn limit"
        return state

    async def _run_planned(
        self,
        state: AgentState,
        *,
        start_index: int,
        on_state_change: Callable[[AgentState], Awaitable[None]] | None = None,
    ) -> AgentState:
        state.status = AgentRunStatus.RUNNING
        for request in state.planned_requests[start_index:]:
            if not await self._schedule_request(state, request):
                await self._notify(state, on_state_change)
                return state
            await self._notify(state, on_state_change)
        state.status = AgentRunStatus.COMPLETED
        return state

    @staticmethod
    async def _notify(
        state: AgentState,
        callback: Callable[[AgentState], Awaitable[None]] | None,
    ) -> None:
        if callback is not None:
            await callback(state)

    @staticmethod
    def _planner_failed(state: AgentState, error: Exception) -> None:
        state.status = AgentRunStatus.FAILED
        state.failure_code = "PLANNER_FAILED"
        state.failure_reason = (
            str(error)[:500] if isinstance(error, PlanningError) else type(error).__name__
        )

    async def _schedule_request(self, state: AgentState, request: ToolCallRequest) -> bool:
        try:
            validate_agent_request(state.task_id, request)
        except CorrelationError:
            state.status = AgentRunStatus.FAILED
            return False
        result = await self.scheduler.schedule(request)
        try:
            validate_agent_result(request, result)
        except CorrelationError as error:
            result = ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=ExecutionStatus.FAILED,
                error=str(error),
                error_code=error.error_code,
            )
        state.results.append(result)
        return self._apply_result_status(state, result)

    @staticmethod
    def _apply_result_status(state: AgentState, result: ToolExecutionResult) -> bool:
        if result.status is ExecutionStatus.WAITING_APPROVAL:
            state.status = AgentRunStatus.WAITING_APPROVAL
            return False
        if result.status is ExecutionStatus.BLOCKED:
            state.status = AgentRunStatus.BLOCKED
            return False
        if result.status is not ExecutionStatus.COMMITTED:
            state.status = AgentRunStatus.FAILED
            return False
        return True
