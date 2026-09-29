from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ra_agent.agent import AgentRuntime
from ra_agent.contracts import (
    APIResponse,
    Conversation,
    ConversationCreateRequest,
    ConversationMessage,
    ConversationMessageCreateRequest,
    ConversationTurnResponse,
    SecurityProfile,
    SecurityProfileUpdate,
)
from ra_agent.core.container import ServiceContainer
from ra_agent.core.ids import new_id
from ra_agent.database.workbench_store import ConversationStore, SecurityProfileStore

from .deps import get_agent_runner, get_services
from .tasks import contract_from_profile, start_agent_task

profile_router = APIRouter(prefix="/api/security-profiles", tags=["security-profiles"])
conversation_router = APIRouter(prefix="/api/conversations", tags=["conversations"])
tools_router = APIRouter(prefix="/api/tools", tags=["tools"])


def _profile_store(request: Request) -> SecurityProfileStore:
    return request.app.state.security_profile_store


def _conversation_store(request: Request) -> ConversationStore:
    return request.app.state.conversation_store


def _profile_view(payload: dict[str, Any], services: ServiceContainer) -> SecurityProfile:
    known = set(services.tool_registry.names())
    return SecurityProfile.model_validate(
        {
            **payload,
            "denied_actions": sorted(known - set(payload["allowed_actions"])),
        }
    )


def _validate_profile(update: SecurityProfileUpdate, services: ServiceContainer) -> None:
    known = set(services.tool_registry.names())
    actions = update.allowed_actions
    if len(actions) != len(set(actions)):
        raise HTTPException(status_code=422, detail="allowed_actions contains duplicates")
    unknown = sorted(set(actions) - known)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown allowed actions: {unknown}")
    unknown_approval = sorted(set(update.approval_policy.required_actions) - known)
    if unknown_approval:
        raise HTTPException(status_code=422, detail=f"Unknown approval actions: {unknown_approval}")
    for scope in update.resource_scopes:
        normalized = scope.replace("\\", "/")
        if (
            not normalized.strip()
            or PurePosixPath(normalized).is_absolute()
            or PureWindowsPath(scope).is_absolute()
            or ".." in PurePosixPath(normalized).parts
        ):
            raise HTTPException(
                status_code=422,
                detail=f"Resource scope must stay relative to the Aegis workspace: {scope}",
            )


@profile_router.get("/{profile_id}")
async def get_profile(
    profile_id: str,
    request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
    version: int | None = Query(default=None, gt=0),
) -> APIResponse[SecurityProfile]:
    payload = await _profile_store(request).get(profile_id, version)
    if payload is None and profile_id == "default" and version is None:
        payload = await _profile_store(request).ensure_default()
    if payload is None:
        raise HTTPException(status_code=404, detail="Unknown security profile")
    return APIResponse(data=_profile_view(payload, services))


@profile_router.put("/{profile_id}")
async def update_profile(
    profile_id: str,
    update: SecurityProfileUpdate,
    request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[SecurityProfile]:
    _validate_profile(update, services)
    payload = await _profile_store(request).create_version(
        profile_id, update.model_dump(mode="json")
    )
    return APIResponse(data=_profile_view(payload, services))


@tools_router.get("")
async def list_tools(
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[list[dict[str, object]]]:
    return APIResponse(data=services.tool_registry.planner_tools())


def _conversation_view(
    payload: dict[str, Any], messages: list[dict[str, Any]] | None = None
) -> Conversation:
    return Conversation.model_validate({**payload, "messages": messages or []})


@conversation_router.post("")
async def create_conversation(
    body: ConversationCreateRequest,
    request: Request,
) -> APIResponse[Conversation]:
    if await _profile_store(request).get(body.security_profile_id) is None:
        if body.security_profile_id == "default":
            await _profile_store(request).ensure_default()
        else:
            raise HTTPException(status_code=404, detail="Unknown security profile")
    now = datetime.now(UTC).isoformat()
    payload = await _conversation_store(request).create(
        {
            "conversation_id": new_id("conversation"),
            "title": body.title.strip()
            if body.title and body.title.strip()
            else "New conversation",
            "security_profile_id": body.security_profile_id,
            "context_summary": "",
            "created_at": now,
            "updated_at": now,
        }
    )
    return APIResponse(data=_conversation_view(payload))


@conversation_router.get("")
async def list_conversations(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
) -> APIResponse[list[Conversation]]:
    payloads = await _conversation_store(request).list(limit=limit)
    return APIResponse(data=[_conversation_view(item) for item in payloads])


@conversation_router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    request: Request,
) -> APIResponse[Conversation]:
    payload = await _conversation_store(request).get(conversation_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Unknown conversation")
    messages = await _conversation_store(request).list_messages(conversation_id)
    return APIResponse(data=_conversation_view(payload, messages))


def _compact_context(
    messages: list[dict[str, Any]], *, recent_limit: int, summary_character_limit: int
) -> tuple[str, list[dict[str, str]]]:
    older = messages[:-recent_limit]
    recent = messages[-recent_limit:]
    summary_lines = [
        f"{item['role']}: {str(item['content']).replace(chr(10), ' ')[:240]}" for item in older
    ]
    summary = "\n".join(summary_lines)[-summary_character_limit:]
    context: list[dict[str, str]] = []
    if summary:
        context.append({"role": "system", "content": f"Earlier conversation summary:\n{summary}"})
    context.extend({"role": str(item["role"]), "content": str(item["content"])} for item in recent)
    return summary, context


@conversation_router.post("/{conversation_id}/messages")
async def create_message(
    conversation_id: str,
    body: ConversationMessageCreateRequest,
    request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
    agent_runner: Annotated[AgentRuntime, Depends(get_agent_runner)],
) -> APIResponse[ConversationTurnResponse]:
    conversation = await _conversation_store(request).get(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Unknown conversation")
    tasks = await request.app.state.task_store.list(limit=200)
    active = next(
        (
            item
            for item in tasks
            if item.get("conversation_id") == conversation_id
            and item.get("status") in {"PLANNING", "RUNNING", "WAITING_APPROVAL"}
        ),
        None,
    )
    if active is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Conversation already has an active task: {active['task_id']}",
        )
    profile = await _profile_store(request).get(conversation["security_profile_id"])
    if profile is None:
        raise HTTPException(status_code=409, detail="Conversation security profile is missing")
    history = await _conversation_store(request).list_messages(conversation_id)
    if not history and conversation["title"] == "New conversation":
        await _conversation_store(request).update_title(
            conversation_id, body.content.strip().replace("\n", " ")[:60]
        )
    settings = request.app.state.runtime_settings
    summary, context = _compact_context(
        history,
        recent_limit=settings.conversation_recent_messages,
        summary_character_limit=settings.conversation_summary_characters,
    )
    await _conversation_store(request).update_summary(conversation_id, summary)
    now = datetime.now(UTC).isoformat()
    message_payload = await _conversation_store(request).add_message(
        {
            "message_id": new_id("message"),
            "conversation_id": conversation_id,
            "role": "user",
            "content": body.content.strip(),
            "task_id": None,
            "created_at": now,
        }
    )
    contract = contract_from_profile(profile)
    run = await start_agent_task(
        http_request=request,
        objective=body.content.strip(),
        contract=contract,
        services=services,
        agent_runner=agent_runner,
        conversation_id=conversation_id,
        user_message_id=message_payload["message_id"],
        context_messages=context,
    )
    await _conversation_store(request).set_message_task(message_payload["message_id"], run.task_id)
    message_payload["task_id"] = run.task_id
    return APIResponse(
        data=ConversationTurnResponse(
            conversation_id=conversation_id,
            message=ConversationMessage.model_validate(message_payload),
            task_id=run.task_id,
            status=run.status,
        )
    )
