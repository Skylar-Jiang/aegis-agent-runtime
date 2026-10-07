"""Aegis-Intent contract and integration API.

These endpoints expose Person 1's frozen schemas without coupling the frontend to
internal storage details. Person 3 can replace the detector behind IntentEnforcer while
keeping this API stable.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from ra_agent.contracts import APIResponse
from ra_agent.contracts.common import ContractModel
from ra_agent.intent import (
    CorrectionPlan,
    CorrectionStatus,
    EffectCheck,
    EffectStatus,
    ExtractionHints,
    IntentSpec,
    RuleBasedIntentExtractor,
    TaskContract,
)

router = APIRouter(prefix="/api/v1/intent", tags=["aegis-intent"])


class IntentDraftRequest(ContractModel):
    task_id: str = Field(min_length=1)
    request_text: str = Field(min_length=1)
    scope: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    resources: list[str] | None = None
    tools: list[str] = Field(default_factory=list)
    permissions: dict[str, Any] = Field(default_factory=dict)


class IntentIssueView(ContractModel):
    code: str
    severity: str
    message: str


class IntentDraftView(ContractModel):
    intent: IntentSpec
    contract: TaskContract
    issues: list[IntentIssueView] = Field(default_factory=list)
    confirmation_required: bool


class IntentConfirmRequest(ContractModel):
    confirmed_by: str = Field(min_length=1)


class IntentUpdateRequest(ContractModel):
    goal: str | None = Field(default=None, min_length=1)
    scope: list[str] | None = None
    allowed_actions: list[str] | None = None
    forbidden_actions: list[str] | None = None
    success_criteria: list[str] | None = None
    source_refs: list[str] | None = None
    tools: list[str] | None = None


class IntentVersionView(ContractModel):
    intent: IntentSpec
    contract: TaskContract
    confirmation_required: bool
    expansion_detected: bool = False


class CorrectionCreateRequest(ContractModel):
    task_id: str = Field(min_length=1)
    base_contract_version: int = Field(ge=1)
    contaminated_refs: list[str] = Field(default_factory=list)
    proposed_actions: list[str] = Field(default_factory=list)
    added_scope: list[str] = Field(default_factory=list)
    retry_budget: int = Field(default=1, ge=0)


class CorrectionStatusRequest(ContractModel):
    status: CorrectionStatus


class EffectCheckRequest(ContractModel):
    request_id: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    normalized_target: str = Field(min_length=1)
    before_digest: str | None = None
    after_digest: str | None = None
    side_effect_ref: str | None = None
    status: EffectStatus


def _extractor(request: Request) -> RuleBasedIntentExtractor:
    return request.app.state.intent_extractor


@router.post("/drafts")
async def create_intent_draft(
    body: IntentDraftRequest,
    request: Request,
) -> APIResponse[IntentDraftView]:
    try:
        intent, issues = _extractor(request).extract(
            task_id=body.task_id,
            request_text=body.request_text,
            hints=ExtractionHints(
                scope=tuple(body.scope),
                allowed_actions=tuple(body.allowed_actions),
                forbidden_actions=tuple(body.forbidden_actions),
                success_criteria=tuple(body.success_criteria),
                source_refs=tuple(body.source_refs),
            ),
        )
        intent, contract = await request.app.state.intent_registry.create(
            intent,
            resources=body.resources,
            tools=body.tools,
            permissions=body.permissions or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    issue_views = [
        IntentIssueView(code=item.code, severity=item.severity, message=item.message)
        for item in issues
    ]
    confirmation_required = bool(issue_views) or intent.confirmed_by is None
    return APIResponse(
        data=IntentDraftView(
            intent=intent,
            contract=contract,
            issues=issue_views,
            confirmation_required=confirmation_required,
        )
    )


@router.post("/{intent_id}/confirm")
async def confirm_intent(
    intent_id: str,
    body: IntentConfirmRequest,
    request: Request,
) -> APIResponse[IntentVersionView]:
    try:
        intent, contract = await request.app.state.intent_registry.confirm(
            intent_id,
            confirmed_by=body.confirmed_by,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown intent") from exc
    return APIResponse(
        data=IntentVersionView(
            intent=intent,
            contract=contract,
            confirmation_required=False,
        )
    )


@router.post("/{intent_id}/versions")
async def create_intent_version(
    intent_id: str,
    body: IntentUpdateRequest,
    request: Request,
) -> APIResponse[IntentVersionView]:
    try:
        intent, contract, expansion = await request.app.state.intent_registry.update(
            intent_id,
            goal=body.goal,
            scope=body.scope,
            allowed_actions=body.allowed_actions,
            forbidden_actions=body.forbidden_actions,
            success_criteria=body.success_criteria,
            source_refs=body.source_refs,
            tools=body.tools,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown intent") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Every new version is a DRAFT. Expansion makes the reason especially important,
    # but even narrowing changes must be explicitly confirmed before it becomes active.
    return APIResponse(
        data=IntentVersionView(
            intent=intent,
            contract=contract,
            confirmation_required=True,
            expansion_detected=expansion,
        )
    )


@router.get("/tasks/{task_id}")
async def get_task_intent(
    task_id: str,
    request: Request,
) -> APIResponse[IntentVersionView]:
    bound = await request.app.state.intent_registry.get_for_task(task_id)
    if bound is None:
        raise HTTPException(status_code=404, detail="Task has no IntentSpec")
    intent, contract = bound
    return APIResponse(
        data=IntentVersionView(
            intent=intent,
            contract=contract,
            confirmation_required=intent.confirmed_by is None,
        )
    )


@router.post("/corrections")
async def create_correction_plan(
    body: CorrectionCreateRequest,
    request: Request,
) -> APIResponse[CorrectionPlan]:
    plan = await request.app.state.intent_correction_manager.create_plan(
        task_id=body.task_id,
        base_contract_version=body.base_contract_version,
        contaminated_refs=body.contaminated_refs,
        proposed_actions=body.proposed_actions,
        added_scope=body.added_scope,
        retry_budget=body.retry_budget,
    )
    return APIResponse(data=plan)


@router.post("/corrections/{plan_id}/status")
async def update_correction_status(
    plan_id: str,
    body: CorrectionStatusRequest,
    request: Request,
) -> APIResponse[CorrectionPlan]:
    try:
        plan = await request.app.state.intent_correction_manager.set_status(plan_id, body.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown correction plan") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return APIResponse(data=plan)


@router.get("/corrections/{plan_id}")
async def get_correction_plan(
    plan_id: str,
    request: Request,
) -> APIResponse[CorrectionPlan]:
    plan = await request.app.state.intent_correction_manager.get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Unknown correction plan")
    return APIResponse(data=plan)


@router.post("/effects")
async def record_effect_check(
    body: EffectCheckRequest,
    request: Request,
) -> APIResponse[EffectCheck]:
    effect = await request.app.state.intent_correction_manager.record_effect(
        request_id=body.request_id,
        tool=body.tool,
        normalized_target=body.normalized_target,
        before_digest=body.before_digest,
        after_digest=body.after_digest,
        side_effect_ref=body.side_effect_ref,
        status=body.status,
    )
    return APIResponse(data=effect)


@router.get("/effects/{request_id}")
async def get_effect_check(
    request_id: str,
    request: Request,
) -> APIResponse[EffectCheck]:
    effect = await request.app.state.intent_correction_manager.get_effect(request_id)
    if effect is None:
        raise HTTPException(status_code=404, detail="No effect check for request_id")
    return APIResponse(data=effect)


@router.get("/decisions/{request_id}")
async def get_intent_decision(request_id: str, request: Request) -> APIResponse[dict[str, Any]]:
    state = await request.app.state.core_state_store.get("requests", request_id)
    if state is None or state.get("intent_decision") is None:
        raise HTTPException(status_code=404, detail="No Intent decision for request_id")
    return APIResponse(
        data={
            "decision": state["intent_decision"],
            "decision_digest": state.get("intent_decision_digest"),
        }
    )
