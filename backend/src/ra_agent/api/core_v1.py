"""Aegis Core v1 PR2 API: real gateway/event/crypto/confirmation integration."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from functools import partial
from typing import Annotated, Any

from anyio.to_thread import run_sync
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ra_agent.audit import VerificationResult
from ra_agent.audit.integrity import MAX_AUDIT_BUNDLE_BYTES
from ra_agent.audit.verifier import VERIFICATION_UNAVAILABLE_CODES
from ra_agent.confirmations import ConfirmationConflictError
from ra_agent.contracts import APIResponse
from ra_agent.contracts.core_service import ContractService
from ra_agent.contracts.core_v1 import (
    ConfirmationPolicyV1,
    ConfirmationRecord,
    ConfirmationResolveRequest,
    ContractConfirmRequest,
    ContractPermissionRule,
    ContractRecord,
    EffectivePermission,
    GatewayEvaluationResult,
    GatewayExecutionResult,
    SessionCreateRequestV1,
    SessionTaskCreateRequestV1,
    SessionTaskDraftV1,
    SessionViewV1,
    TaskContractCreateRequest,
    TaskContractUpdateRequest,
    ToolEvaluationRequest,
    ToolReplanRequest,
)
from ra_agent.core.container import ServiceContainer
from ra_agent.core.ids import new_id
from ra_agent.crypto import CryptoError
from ra_agent.events import EventStoreError
from ra_agent.execution._cancellation import complete_before_cancelling
from ra_agent.gateway import GatewayError, ToolExecutionRejected, ToolGateway

from .core_lifecycle import complete_core_confirmation, set_core_task_status
from .deps import get_services

router = APIRouter(prefix="/api/v1", tags=["aegis-core-v1"])


class AuditExportRequest(BaseModel):
    task_id: str = Field(min_length=1)
    checkpoint_id: str = Field(min_length=1)


class AuditVerifyRequest(BaseModel):
    bundle: dict[str, Any]
    trusted_checkpoint_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)


def _contract_service(request: Request) -> ContractService:
    return request.app.state.core_contract_service


def _gateway(request: Request) -> ToolGateway:
    return request.app.state.core_gateway


def _session_users(request: Request) -> dict[str, str]:
    return request.app.state.core_session_users


def _tool_manifest_digest(services: ServiceContainer) -> str:
    payload = services.tool_registry.planner_tools()
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _rules_from_profile(
    profile: dict[str, Any], services: ServiceContainer
) -> tuple[list[ContractPermissionRule], list[ContractPermissionRule]]:
    resources = list(profile.get("resource_scopes") or ["*"])
    allowed_actions = list(profile.get("allowed_actions") or [])
    allowed = [
        ContractPermissionRule(tool=action, action=action, resource=resource, effect="*")
        for action in allowed_actions
        for resource in resources
    ]
    denied_actions = sorted(set(services.tool_registry.names()) - set(allowed_actions))
    denied = [
        ContractPermissionRule(tool=action, action=action, resource="*", effect="*")
        for action in denied_actions
    ]
    return allowed, denied


def _crypto_http_error(exc: CryptoError) -> HTTPException:
    status = 503 if exc.code in VERIFICATION_UNAVAILABLE_CODES else 409
    if exc.code == "INPUT_TOO_LARGE":
        status = 413
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


def _event_http_error(exc: EventStoreError) -> HTTPException:
    status = 503 if exc.code in {"STORAGE_UNAVAILABLE", "CHECK_UNAVAILABLE"} else 409
    return HTTPException(status_code=status, detail={"code": exc.code, "message": str(exc)})


@router.get("/health")
async def core_health(request: Request) -> APIResponse[dict[str, object]]:
    gateway = _gateway(request)
    return APIResponse(
        data={
            "status": "ok",
            "core_version": "v1-pr2",
            "contract_service": type(_contract_service(request)).__name__,
            "event_store": type(gateway.event_store).__name__,
            "signature_provider": type(gateway.signature_provider).__name__,
            "public_keys": list(gateway.signature_provider.list_public_keys()),
            "crypto_mode": request.app.state.core_crypto_mode.value,
            "limits": {
                "audit_bundle_bytes": MAX_AUDIT_BUNDLE_BYTES,
                "max_read_bytes": request.app.state.runtime_settings.max_read_bytes,
                "max_backup_bytes": (
                    request.app.state.runtime_settings.max_backup_bytes
                    or request.app.state.runtime_settings.max_read_bytes
                ),
                "max_write_bytes": request.app.state.runtime_settings.max_write_bytes,
                "max_list_entries": request.app.state.runtime_settings.max_list_entries,
            },
            "confirmation_service": type(request.app.state.core_confirmation_service).__name__,
            "tool_executor": type(request.app.state.core_tool_executor).__name__,
            "evidence_recorder": (
                type(request.app.state.core_evidence_recorder).__name__
                if request.app.state.core_evidence_recorder is not None
                else None
            ),
            "audit_exporter": (
                type(request.app.state.core_audit_exporter).__name__
                if request.app.state.core_audit_exporter is not None
                else None
            ),
            "audit_verifier": (
                type(request.app.state.core_audit_verifier).__name__
                if request.app.state.core_audit_verifier is not None
                else None
            ),
        }
    )


@router.post("/sessions")
async def create_session(
    body: SessionCreateRequestV1,
    request: Request,
) -> APIResponse[SessionViewV1]:
    profile_store = request.app.state.security_profile_store
    profile = await profile_store.get(body.security_profile_id)
    if profile is None and body.security_profile_id == "default":
        profile = await profile_store.ensure_default()
    if profile is None:
        raise HTTPException(status_code=404, detail="Unknown security profile")

    now = datetime.now(UTC).isoformat()
    session_id = new_id("session")
    title = body.title.strip() if body.title and body.title.strip() else "New Core session"
    await request.app.state.conversation_store.create(
        {
            "conversation_id": session_id,
            "title": title,
            "security_profile_id": body.security_profile_id,
            "context_summary": "",
            "created_at": now,
            "updated_at": now,
        }
    )
    _session_users(request)[session_id] = body.user_id
    await asyncio.to_thread(
        request.app.state.core_state_store.set_session,
        session_id,
        {
            "conversation_id": session_id,
            "user_id": body.user_id,
            "security_profile_id": body.security_profile_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
        },
    )
    return APIResponse(
        data=SessionViewV1(
            session_id=session_id,
            user_id=body.user_id,
            title=title,
            security_profile_id=body.security_profile_id,
            created_at=datetime.fromisoformat(now),
        )
    )


@router.post("/sessions/{session_id}/tasks")
async def create_session_task(
    session_id: str,
    body: SessionTaskCreateRequestV1,
    request: Request,
    services: Annotated[ServiceContainer, Depends(get_services)],
) -> APIResponse[SessionTaskDraftV1]:
    session = await request.app.state.conversation_store.get(session_id)
    binding = await asyncio.to_thread(request.app.state.core_state_store.get_session, session_id)
    if session is None:
        session = binding
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session")
    user_id = binding.get("user_id") if binding else _session_users(request).get(session_id)
    if user_id is None:
        raise HTTPException(status_code=409, detail="Session user binding is unavailable")
    profile = await request.app.state.security_profile_store.get(session["security_profile_id"])
    if profile is None:
        raise HTTPException(status_code=409, detail="Session security profile is unavailable")

    task_id = new_id("task")
    allowed, denied = _rules_from_profile(profile, services)
    record = await _contract_service(request).create_contract(
        TaskContractCreateRequest(
            session_id=session_id,
            task_id=task_id,
            user_id=user_id,
            goals=[body.objective.strip()],
            completion_criteria=body.completion_criteria,
            allowed=allowed,
            denied=denied,
            limits={"max_affected_objects": int(profile.get("max_affected_objects", 100))},
            confirmation=ConfirmationPolicyV1(
                required_actions=list(
                    profile.get("approval_policy", {}).get("required_actions", [])
                )
            ),
            policy_version=f"{session['security_profile_id']}:{profile['version']}",
            tool_manifest_digest=_tool_manifest_digest(services),
        )
    )
    await _gateway(request).record_contract_event(
        record, event_type="CONTRACT_CREATED", actor=user_id
    )
    now = datetime.now(UTC).isoformat()
    await request.app.state.task_store.create(
        {
            "task_id": task_id,
            "objective": body.objective.strip(),
            "status": "DRAFT",
            "created_at": now,
            "updated_at": now,
            "final_answer": None,
            "contract": record.contract.model_dump(mode="json"),
            "agent_state": None,
            "conversation_id": session_id,
            "security_profile_id": session["security_profile_id"],
            "security_profile_version": profile["version"],
        }
    )
    await set_core_task_status(request, task_id, "DRAFT")
    return APIResponse(
        data=SessionTaskDraftV1(
            task_id=task_id,
            session_id=session_id,
            status="DRAFT",
            contract=record,
        )
    )


@router.post("/contracts")
async def create_contract(
    body: TaskContractCreateRequest,
    request: Request,
) -> APIResponse[ContractRecord]:
    record = await _contract_service(request).create_contract(body)
    await _gateway(request).record_contract_event(
        record, event_type="CONTRACT_CREATED", actor=body.user_id
    )
    return APIResponse(data=record)


@router.post("/contracts/{contract_id}/versions")
async def create_contract_version(
    contract_id: str,
    body: TaskContractUpdateRequest,
    request: Request,
) -> APIResponse[ContractRecord]:
    async with request.app.state.core_admission_guard():
        try:
            record = await _contract_service(request).update_contract(contract_id, body)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown contract") from exc
        await _gateway(request).record_contract_event(
            record, event_type="CONTRACT_UPDATED", actor="contract-api"
        )
        return APIResponse(data=record)


@router.post("/contracts/{contract_id}/confirm")
async def confirm_contract(
    contract_id: str,
    body: ContractConfirmRequest,
    request: Request,
) -> APIResponse[ContractRecord]:
    async with request.app.state.core_admission_guard():
        try:
            record = await _contract_service(request).confirm_contract(
                contract_id,
                version=body.version,
                confirmed_by=body.confirmed_by,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown contract version") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await complete_before_cancelling(complete_core_confirmation(request, record))
    return APIResponse(data=record)


@router.get("/permissions/effective")
async def get_effective_permission(
    request: Request,
    request_id: str = Query(min_length=1),
) -> APIResponse[EffectivePermission]:
    effective = await asyncio.to_thread(_gateway(request).effective_permission_for, request_id)
    if effective is None:
        raise HTTPException(status_code=404, detail="No permission result for request_id")
    return APIResponse(data=effective)


@router.post("/tool-calls/evaluate")
async def evaluate_tool_call(
    body: ToolEvaluationRequest,
    request: Request,
) -> APIResponse[GatewayEvaluationResult]:
    try:
        result = await _gateway(request).evaluate(body.envelope, body.permissions)
        if result.intent is not None and result.intent.disposition == "SAFE_STOP":
            await set_core_task_status(request, body.envelope.task_id, "CANCELLED")
    except (GatewayError, ConfirmationConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=result)


@router.post("/tool-calls/{request_id}/execute")
async def execute_tool_call(
    request_id: str,
    request: Request,
) -> APIResponse[GatewayExecutionResult]:
    async with request.app.state.core_admission_guard():
        try:
            result = await _gateway(request).execute(request_id)
        except GatewayError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ToolExecutionRejected as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "TOOL_EXECUTION_REJECTED", "message": str(exc)},
            ) from exc
        state = await request.app.state.core_state_store.get("requests", request_id)
        if state is not None:
            await set_core_task_status(request, state["envelope"]["task_id"], "EXECUTED")
    return APIResponse(data=result)


@router.post("/tool-calls/{request_id}/replan")
async def replan_tool_call(
    request_id: str,
    body: ToolReplanRequest,
    request: Request,
) -> APIResponse[ContractRecord]:
    async with request.app.state.core_admission_guard():
        try:
            record = await _gateway(request).replan(request_id, body.changes)
        except (GatewayError, KeyError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return APIResponse(data=record)


@router.get("/confirmations/{confirmation_id}")
async def get_confirmation(
    confirmation_id: str,
    request: Request,
) -> APIResponse[ConfirmationRecord]:
    record = await request.app.state.core_confirmation_service.get_confirmation(confirmation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown confirmation_id")
    return APIResponse(data=record)


@router.post("/confirmations/{confirmation_id}/resolve")
async def resolve_confirmation(
    confirmation_id: str,
    body: ConfirmationResolveRequest,
    request: Request,
) -> APIResponse[GatewayEvaluationResult]:
    async with request.app.state.core_admission_guard():
        try:
            result = await _gateway(request).resolve_confirmation(
                confirmation_id,
                confirmed=body.confirmed,
                resolved_by=body.resolved_by,
            )
        except GatewayError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return APIResponse(data=result)


@router.get("/tasks/{task_id}/events")
async def list_task_events(
    task_id: str,
    request: Request,
    after_sequence: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1, le=1000),
) -> APIResponse[list[dict[str, Any]]]:
    store = request.app.state.core_event_store
    try:
        reader = getattr(store, "list_task_event_page", None)
        if limit is not None and reader is not None:
            events = await reader(task_id, after_sequence=after_sequence, limit=limit)
        else:
            events = [
                event
                for event in await store.list_task_events(task_id)
                if event["sequence"] > after_sequence
            ]
            if limit is not None:
                events = events[:limit]
    except EventStoreError as exc:
        raise _event_http_error(exc) from exc
    return APIResponse(data=events)


@router.post("/audit/export")
async def export_audit_bundle(
    body: AuditExportRequest,
    request: Request,
) -> APIResponse[dict[str, Any]]:
    exporter = request.app.state.core_audit_exporter
    if exporter is None:
        raise HTTPException(
            status_code=503,
            detail="Signed audit export requires CORE_CRYPTO_MODE=sm2",
        )
    try:
        async with request.app.state.core_audit_limiter:
            bundle = await exporter.export_task(
                task_id=body.task_id,
                checkpoint_id=body.checkpoint_id,
            )
    except CryptoError as exc:
        raise _crypto_http_error(exc) from exc
    except EventStoreError as exc:
        raise _event_http_error(exc) from exc
    return APIResponse(data=bundle)


@router.post("/audit/verify")
async def verify_audit_bundle(
    body: AuditVerifyRequest,
    request: Request,
) -> APIResponse[VerificationResult]:
    verifier = request.app.state.core_audit_verifier
    if verifier is None:
        raise HTTPException(
            status_code=503,
            detail="Independent audit verification requires CORE_CRYPTO_MODE=sm2",
        )
    result = await run_sync(
        partial(
            verifier.verify_bundle,
            body.bundle,
            trusted_checkpoint_id=body.trusted_checkpoint_id,
            task_id=body.task_id,
        ),
        limiter=request.app.state.core_audit_limiter,
    )
    return APIResponse(data=result)
