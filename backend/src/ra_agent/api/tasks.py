import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ra_agent.agent import AgentRunStatus, AgentRuntime, AgentState
from ra_agent.contracts import (
    APIResponse,
    AuditEventType,
    TaskContract,
    TaskCreateRequest,
    TaskResponse,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.core.container import ServiceContainer
from ra_agent.core.ids import new_id
from ra_agent.database.task_store import TaskStore
from ra_agent.database.workbench_store import ConversationStore, SecurityProfileStore

from .core_lifecycle import set_core_task_status
from .deps import get_agent_runner, get_services
from .task_graphs import is_known_graph_task, list_approval_views

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@dataclass
class TaskRun:
    task_id: str
    objective: str
    created_at: datetime
    contract: TaskContract | None = None
    status: str = "RUNNING"
    final_answer: str | None = None
    state: AgentState | None = None
    background: asyncio.Task[None] | None = None
    conversation_id: str | None = None
    user_message_id: str | None = None
    context_messages: list[dict[str, str]] | None = None


def _runs(request: Request) -> dict[str, TaskRun]:
    runs = getattr(request.app.state, "task_runs", None)
    if runs is None:
        runs = {}
        request.app.state.task_runs = runs
    return runs


def _store(request: Request) -> TaskStore:
    return request.app.state.task_store


def _serialize_state(state: AgentState | None) -> dict[str, Any] | None:
    if state is None:
        return None
    return {
        "task_id": state.task_id,
        "objective": state.objective,
        "planned_requests": [item.model_dump(mode="json") for item in state.planned_requests],
        "results": [item.model_dump(mode="json") for item in state.results],
        "context_messages": state.context_messages,
        "status": state.status.value,
        "final_answer": state.final_answer,
        "failure_code": state.failure_code,
        "failure_reason": state.failure_reason,
    }


def _restore_state(payload: object) -> AgentState | None:
    if not isinstance(payload, dict):
        return None
    try:
        return AgentState(
            task_id=str(payload["task_id"]),
            objective=str(payload["objective"]),
            planned_requests=[
                ToolCallRequest.model_validate(item) for item in payload.get("planned_requests", [])
            ],
            results=[
                ToolExecutionResult.model_validate(item) for item in payload.get("results", [])
            ],
            context_messages=[
                {"role": str(item["role"]), "content": str(item["content"])}
                for item in payload.get("context_messages", [])
                if isinstance(item, dict) and "role" in item and "content" in item
            ],
            status=AgentRunStatus(str(payload["status"])),
            final_answer=payload.get("final_answer"),
            failure_code=payload.get("failure_code"),
            failure_reason=payload.get("failure_reason"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _public_task(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "agent_state"}


async def _persist_state(run: TaskRun, store: TaskStore) -> None:
    await store.update(
        run.task_id,
        status=run.status,
        final_answer=run.final_answer,
        contract=run.contract.model_dump(mode="json") if run.contract is not None else None,
        agent_state=_serialize_state(run.state),
    )


async def _record_finished(
    run: TaskRun, services: ServiceContainer, *, summary: str | None = None
) -> None:
    await services.audit_recorder.record(
        task_id=run.task_id,
        event_type=AuditEventType.TASK_FINISHED,
        actor="api",
        status=run.status,
        summary=summary or f"Task finished: {run.status}",
    )


async def _finalize_state(
    run: TaskRun,
    state: AgentState,
    services: ServiceContainer,
    store: TaskStore,
) -> None:
    run.state = state
    run.status = state.status.value
    run.final_answer = state.final_answer if run.status == "COMPLETED" else None
    await _persist_state(run, store)
    if state.failure_code == "PLANNER_FAILED":
        await services.audit_recorder.record(
            task_id=run.task_id,
            event_type=AuditEventType.PLANNER_FAILED,
            actor="agent-runtime",
            status="FAILED",
            summary="Planner failed before tool scheduling",
            details={
                "error_code": state.failure_code,
                "reason": state.failure_reason or "PlannerError",
            },
        )
    if state.status is AgentRunStatus.WAITING_APPROVAL:
        return
    await _record_finished(run, services)


async def _run_task(
    run: TaskRun,
    agent_runner: AgentRuntime,
    services: ServiceContainer,
    store: TaskStore,
    conversation_store: ConversationStore | None = None,
) -> None:
    async def save_progress(state: AgentState) -> None:
        run.state = state
        run.status = state.status.value
        await _persist_state(run, store)

    try:
        if isinstance(agent_runner, AgentRuntime):
            state = await agent_runner.run(
                run.task_id,
                run.objective,
                run.contract,
                context_messages=run.context_messages,
                on_state_change=save_progress,
            )
        else:
            state = await agent_runner.run(run.task_id, run.objective, run.contract)
        await _finalize_state(run, state, services, store)
        await _append_assistant_message(run, conversation_store)
    except asyncio.CancelledError:
        run.status = "CANCELLED"
        run.final_answer = None
        await _persist_state(run, store)
        raise
    except Exception as exc:
        run.status = "FAILED"
        run.final_answer = None
        await _persist_state(run, store)
        await services.audit_recorder.record(
            task_id=run.task_id,
            event_type=AuditEventType.STEP_FAILED,
            actor="api",
            status="FAILED",
            summary="Task background execution failed",
            details={"error": str(exc)},
        )
        await _record_finished(run, services, summary="Task failed in background execution")


async def _resume_task(
    run: TaskRun,
    approval_id: str,
    agent_runner: AgentRuntime,
    services: ServiceContainer,
    store: TaskStore,
    conversation_store: ConversationStore | None = None,
) -> None:
    if run.state is None:
        raise ValueError("Agent task has no saved state")

    async def save_progress(state: AgentState) -> None:
        run.state = state
        run.status = state.status.value
        await _persist_state(run, store)

    try:
        state = await agent_runner.resume_after_approval(
            run.state,
            approval_id,
            run.contract,
            on_state_change=save_progress,
        )
        await _finalize_state(run, state, services, store)
        await _append_assistant_message(run, conversation_store)
    except asyncio.CancelledError:
        run.status = "CANCELLED"
        run.final_answer = None
        await _persist_state(run, store)
        raise
    except Exception as exc:
        run.status = "FAILED"
        run.final_answer = None
        await _persist_state(run, store)
        await services.audit_recorder.record(
            task_id=run.task_id,
            event_type=AuditEventType.STEP_FAILED,
            actor="api",
            status="FAILED",
            summary="Task approval resume failed",
            details={"error": str(exc), "approval_id": approval_id},
        )
        await _record_finished(run, services, summary="Task failed while resuming approval")


async def is_known_agent_task(request: Request, task_id: str) -> bool:
    if task_id in _runs(request):
        return True
    return await _store(request).get(task_id) is not None


async def resume_task_for_approval(request: Request, *, task_id: str, approval_id: str) -> None:
    """Resume an Agent task if the approval belongs to one; graphs are handled separately."""

    store = _store(request)
    snapshot = await store.get(task_id)
    if snapshot is None or snapshot["status"] != "WAITING_APPROVAL":
        return
    run = _runs(request).get(task_id)
    if run is None:
        state = _restore_state(snapshot.get("agent_state"))
        if state is None:
            await store.update(task_id, status="INTERRUPTED")
            return
        contract_payload = snapshot.get("contract")
        contract = (
            TaskContract.model_validate(contract_payload)
            if isinstance(contract_payload, dict)
            else None
        )
        run = TaskRun(
            task_id=task_id,
            objective=snapshot["objective"],
            created_at=datetime.fromisoformat(snapshot["created_at"]),
            contract=contract,
            status=snapshot["status"],
            final_answer=snapshot.get("final_answer"),
            state=state,
            conversation_id=snapshot.get("conversation_id"),
            context_messages=state.context_messages,
        )
        _runs(request)[task_id] = run
    if run.background is not None and not run.background.done():
        return
    run.status = "RUNNING"
    await _persist_state(run, store)
    run.background = asyncio.create_task(
        _resume_task(
            run,
            approval_id,
            request.app.state.agent_runner,
            request.app.state.services,
            store,
            getattr(request.app.state, "conversation_store", None),
        )
    )


async def _append_assistant_message(
    run: TaskRun, conversation_store: ConversationStore | None
) -> None:
    if (
        conversation_store is None
        or run.conversation_id is None
        or run.status != "COMPLETED"
        or not run.final_answer
    ):
        return
    await conversation_store.add_message(
        {
            "message_id": new_id("message"),
            "conversation_id": run.conversation_id,
            "role": "assistant",
            "content": run.final_answer,
            "task_id": run.task_id,
            "created_at": datetime.now(UTC).isoformat(),
        }
    )


def contract_from_profile(profile: dict[str, Any]) -> TaskContract:
    approval_policy = profile.get("approval_policy", {})
    return TaskContract(
        allowed_actions=list(profile["allowed_actions"]),
        allowed_resources=list(profile["resource_scopes"]),
        forbidden_actions=[],
        max_affected_objects=int(profile["max_affected_objects"]),
        allow_egress=bool(profile["allow_egress"]),
        approval_required_actions=list(approval_policy.get("required_actions", [])),
        bulk_approval_threshold=int(approval_policy.get("bulk_action_threshold", 20)),
        security_profile_id=str(profile["profile_id"]),
        security_profile_version=int(profile["version"]),
        requires_reconfirmation=False,
    )


async def start_agent_task(
    *,
    http_request: Request,
    objective: str,
    contract: TaskContract,
    services: ServiceContainer,
    agent_runner: AgentRuntime,
    conversation_id: str | None = None,
    user_message_id: str | None = None,
    context_messages: list[dict[str, str]] | None = None,
) -> TaskRun:
    task_id = new_id("task")
    now = datetime.now(UTC)
    await services.audit_recorder.record(
        task_id=task_id,
        event_type=AuditEventType.TASK_CREATED,
        actor="api",
        status="CREATED",
        summary=f"Task created: {objective}",
        details={
            "objective": objective,
            "conversation_id": conversation_id,
            "security_profile_id": contract.security_profile_id,
            "security_profile_version": contract.security_profile_version,
        },
    )
    await _store(http_request).create(
        {
            "task_id": task_id,
            "objective": objective,
            "status": "RUNNING",
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "final_answer": None,
            "contract": contract.model_dump(mode="json"),
            "agent_state": None,
            "conversation_id": conversation_id,
            "security_profile_id": contract.security_profile_id,
            "security_profile_version": contract.security_profile_version,
        }
    )
    run = TaskRun(
        task_id=task_id,
        objective=objective,
        created_at=now,
        contract=contract,
        conversation_id=conversation_id,
        user_message_id=user_message_id,
        context_messages=context_messages,
    )
    _runs(http_request)[task_id] = run
    run.background = asyncio.create_task(
        _run_task(
            run,
            agent_runner,
            services,
            _store(http_request),
            getattr(http_request.app.state, "conversation_store", None),
        )
    )
    return run


@router.post("")
async def create_task(
    request: TaskCreateRequest,
    http_request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
    agent_runner: Annotated[AgentRuntime, Depends(get_agent_runner)],
) -> APIResponse[TaskResponse]:
    try:
        contract = request.contract
        if contract is None:
            profile_store: SecurityProfileStore = http_request.app.state.security_profile_store
            profile = await profile_store.ensure_default()
            contract = contract_from_profile(profile)
        run = await start_agent_task(
            http_request=http_request,
            objective=request.objective,
            contract=contract,
            services=services,
            agent_runner=agent_runner,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="task persistence unavailable") from exc
    return APIResponse(
        data=TaskResponse(
            task_id=run.task_id,
            objective=request.objective,
            status=run.status,
            created_at=run.created_at,
        )
    )


@router.get("/{task_id}")
async def get_task(task_id: str, request: Request) -> APIResponse[dict[str, Any]]:
    payload = await _store(request).get(task_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Unknown task")
    return APIResponse(data=_public_task(payload))


@router.get("")
async def list_tasks(
    request: Request, limit: int = Query(default=50, ge=1, le=200)
) -> APIResponse[list[dict[str, Any]]]:
    return APIResponse(
        data=[_public_task(item) for item in await _store(request).list(limit=limit)]
    )


@router.get("/{task_id}/approvals")
async def list_task_approvals(
    task_id: str,
    request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[list[dict[str, str]]]:
    if not await is_known_agent_task(request, task_id) and not is_known_graph_task(
        request, task_id
    ):
        raise HTTPException(status_code=404, detail="Unknown task")
    return APIResponse(data=await list_approval_views(services, task_id=task_id))


@router.get("/{task_id}/steps")
async def get_steps(task_id: str, request: Request) -> APIResponse[dict[str, object]]:
    payload = await _store(request).get(task_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Unknown task")
    state = _restore_state(payload.get("agent_state"))
    if state is None:
        return APIResponse(data={"task_id": task_id, "steps": []})
    results = {item.request_id: item for item in state.results}
    steps = []
    for item in state.planned_requests:
        result = results.get(item.request_id)
        steps.append(
            {
                "task_id": task_id,
                "step_id": item.step_id,
                "description": item.context_summary,
                "tool_name": item.tool_name,
                "arguments": item.arguments,
                "dependencies": [],
                "status": result.status.value if result is not None else "PLANNED",
                "request_id": item.request_id,
                "error": result.error if result is not None else None,
                "error_code": result.error_code if result is not None else None,
            }
        )
    return APIResponse(data={"task_id": task_id, "steps": steps})


@router.get("/{task_id}/events")
async def get_events(
    task_id: str,
    services: Annotated[ServiceContainer, Depends(get_services)],
    limit: int = 100,
    offset: int = 0,
) -> APIResponse[dict[str, Any]]:
    recorder = services.audit_recorder
    events: list[dict[str, Any]] = []
    if hasattr(recorder, "events_for"):
        try:
            events = await recorder.events_for(  # type: ignore[union-attr]
                task_id, limit=limit, offset=offset
            )
        except TypeError:
            raw = recorder.events_for(task_id)  # type: ignore[union-attr]
            events = [
                {
                    "event_id": event.event_id,
                    "task_id": event.task_id,
                    "step_id": event.step_id,
                    "request_id": event.request_id,
                    "sequence_number": event.sequence_number,
                    "event_type": event.event_type.value,
                    "timestamp": str(event.timestamp),
                    "actor": event.actor,
                    "status": event.status,
                    "risk_level": event.risk_level.value if event.risk_level else None,
                    "decision": event.decision.value if event.decision else None,
                    "summary": event.summary,
                    "details": event.details,
                }
                for event in raw
            ][offset : offset + limit]
    return APIResponse(data={"task_id": task_id, "events": events, "count": len(events)})


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[dict[str, str]]:
    payload = await _store(request).get(task_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Unknown task")
    core_contract = payload.get("contract") or {}
    if "contract_id" in core_contract:
        # An acknowledged cancel is ordered after prior admitted commits and before
        # every later call. Completed effects are retained for explicit recovery.
        async with request.app.state.core_admission_guard():
            await set_core_task_status(request, task_id, "CANCELLED")
            record = await request.app.state.core_contract_service.get_contract_version(
                core_contract["contract_id"], core_contract["version"]
            )
            if record is not None:
                await request.app.state.core_gateway.record_contract_event(
                    record, event_type="TASK_CANCELLED", actor="api", state="CANCELLED"
                )
        return APIResponse(data={"task_id": task_id, "status": "CANCELLED"})
    run = _runs(request).get(task_id)
    interrupt = getattr(request.app.state.agent_runner, "cancel_task", None)
    if interrupt is not None:
        await interrupt(task_id)
    if run is not None and run.background is not None and not run.background.done():
        run.background.cancel()
        run.status = "CANCELLED"
        run.final_answer = None
    await _store(request).update(task_id, status="CANCELLED", final_answer=None)
    await services.audit_recorder.record(
        task_id=task_id,
        event_type=AuditEventType.TASK_CANCELLED,
        actor="api",
        status="CANCELLED",
        summary="Task cancelled by user",
    )
    return APIResponse(data={"task_id": task_id, "status": "CANCELLED"})
