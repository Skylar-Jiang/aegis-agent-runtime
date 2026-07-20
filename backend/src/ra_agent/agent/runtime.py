from typing import Protocol, cast

from ra_agent.contracts import ExecutionStatus, ToolCallRequest, ToolExecutionResult
from ra_agent.runtime.correlation import (
    CorrelationError,
    validate_agent_request,
    validate_agent_result,
)

from .planner import IterativePlanner, Planner
from .state import AgentRunStatus, AgentState


class RuntimeSchedulerPort(Protocol):
    async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult: ...


class AgentRuntime:
    """Minimal Agent state flow; all tool requests go through the Runtime scheduler."""

    def __init__(
        self, *, planner: Planner, scheduler: RuntimeSchedulerPort, max_turns: int = 8
    ) -> None:
        if max_turns <= 0:
            raise ValueError("max_turns must be positive")
        self.planner = planner
        self.scheduler = scheduler
        self.max_turns = max_turns

    async def run(self, task_id: str, objective: str) -> AgentState:
        state = AgentState(task_id=task_id, objective=objective)
        if isinstance(self.planner, IterativePlanner):
            return await self._run_iteratively(state)
        try:
            state.planned_requests = await self.planner.plan(task_id, objective)
        except Exception:
            state.status = AgentRunStatus.FAILED
            return state
        state.status = AgentRunStatus.RUNNING

        for request in state.planned_requests:
            try:
                validate_agent_request(task_id, request)
            except CorrelationError:
                state.status = AgentRunStatus.FAILED
                break
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
            if result.status is ExecutionStatus.WAITING_APPROVAL:
                state.status = AgentRunStatus.WAITING_APPROVAL
                break
            if result.status is ExecutionStatus.BLOCKED:
                state.status = AgentRunStatus.BLOCKED
                break
            if result.status is not ExecutionStatus.COMMITTED:
                state.status = AgentRunStatus.FAILED
                break
        else:
            state.status = AgentRunStatus.COMPLETED
        return state

    async def _run_iteratively(self, state: AgentState) -> AgentState:
        planner = cast(IterativePlanner, self.planner)
        state.status = AgentRunStatus.RUNNING
        for _ in range(self.max_turns):
            try:
                request = await planner.next_request(
                    state.task_id, state.objective, state.results
                )
            except Exception:
                state.status = AgentRunStatus.FAILED
                return state
            if request is None:
                state.status = AgentRunStatus.COMPLETED
                return state
            state.planned_requests.append(request)
            if not await self._schedule_request(state, request):
                return state
        state.status = AgentRunStatus.FAILED
        return state

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
