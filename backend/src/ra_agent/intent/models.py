"""Aegis-Intent public schemas frozen by the four-person development plan.

The field names in this module are intentionally stable. Internal implementations may
change, but cross-module/API consumers should depend on these schemas and bump the
explicit version when semantics change.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from ra_agent.contracts.common import ContractModel, UTCDateTime


class IntentContractStatus(StrEnum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    SUPERSEDED = "SUPERSEDED"


class IntentDecisionType(StrEnum):
    CONTINUE = "CONTINUE"
    REPLAN = "REPLAN"
    REQUEST_CONFIRMATION = "REQUEST_CONFIRMATION"
    BLOCK = "BLOCK"
    SAFE_TERMINATE = "SAFE_TERMINATE"


class CorrectionStatus(StrEnum):
    DRAFT = "DRAFT"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    APPROVED = "APPROVED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    EXHAUSTED = "EXHAUSTED"


class EffectStatus(StrEnum):
    PENDING = "PENDING"
    UNCHANGED = "UNCHANGED"
    APPLIED = "APPLIED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"


class IntentSpec(ContractModel):
    intent_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    scope: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    version: int = Field(ge=1)
    confirmed_by: str | None = None
    confirmed_at: UTCDateTime | None = None

    @field_validator(
        "scope",
        "allowed_actions",
        "forbidden_actions",
        "success_criteria",
        "source_refs",
        mode="before",
    )
    @classmethod
    def normalize_string_lists(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @model_validator(mode="after")
    def validate_action_conflicts(self) -> IntentSpec:
        allowed = {item.casefold() for item in self.allowed_actions}
        forbidden = {item.casefold() for item in self.forbidden_actions}
        overlap = sorted(allowed & forbidden)
        if overlap:
            raise ValueError(f"actions cannot be both allowed and forbidden: {overlap}")
        if (self.confirmed_by is None) != (self.confirmed_at is None):
            raise ValueError("confirmed_by and confirmed_at must be set together")
        return self


class TaskContract(ContractModel):
    """Intent-branch contract view.

    This deliberately does not replace Core ``TaskContractV2``. It is the stable
    Aegis-Intent projection that links the user-approved IntentSpec to runtime tools,
    resources and completion conditions.
    """

    contract_id: str = Field(min_length=1)
    contract_version: int = Field(ge=1)
    intent_ref: str = Field(min_length=1)
    resources: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    permissions: dict[str, Any] = Field(default_factory=dict)
    completion_conditions: list[str] = Field(default_factory=list)
    parent_version: int | None = Field(default=None, ge=1)
    status: IntentContractStatus = IntentContractStatus.DRAFT
    digest: str = Field(min_length=1)

    @staticmethod
    def compute_digest(payload: dict[str, Any]) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class BehaviorEvent(ContractModel):
    event_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    step_index: int = Field(ge=0)
    actor: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    source_ref: str | None = None
    subgoal: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    action: str = Field(min_length=1)
    args_digest: str = Field(min_length=1)
    target: str = Field(min_length=1)
    risk_flags: list[str] = Field(default_factory=list)
    timestamp: UTCDateTime


class DecisionResult(ContractModel):
    decision_id: str = Field(min_length=1)
    decision: IntentDecisionType
    risk_score: float = Field(ge=0.0, le=1.0)
    trigger_dimensions: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    reason_code: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    detector_version: str = Field(min_length=1)
    expires_at: UTCDateTime


class CorrectionPlan(ContractModel):
    plan_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    base_contract_version: int = Field(ge=1)
    contaminated_refs: list[str] = Field(default_factory=list)
    proposed_actions: list[str] = Field(default_factory=list)
    added_scope: list[str] = Field(default_factory=list)
    confirmation_required: bool = False
    retry_budget: int = Field(default=1, ge=0)
    status: CorrectionStatus = CorrectionStatus.DRAFT


class EffectCheck(ContractModel):
    effect_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    normalized_target: str = Field(min_length=1)
    before_digest: str | None = None
    after_digest: str | None = None
    side_effect_ref: str | None = None
    status: EffectStatus


class IntentCheckContext(ContractModel):
    """Trusted runtime input handed to the detector before tool execution."""

    request_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    contract_version: int = Field(ge=1)
    subgoal: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    normalized_action: str = Field(min_length=1)
    normalized_target: str = Field(min_length=1)
    effect_class: str = Field(min_length=1)
    canonical_args: dict[str, Any] = Field(default_factory=dict)
    trusted_state: dict[str, Any] = Field(default_factory=dict)
    # The gateway supplies the recent trusted event summaries.  Detectors may use
    # them for bounded-window sequence features, but must not treat future events
    # or final labels as online input.
    recent_events: list[dict[str, Any]] = Field(default_factory=list)
