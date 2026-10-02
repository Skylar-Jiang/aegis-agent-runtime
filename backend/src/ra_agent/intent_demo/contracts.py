"""Proposed v0.1 demo interfaces, preserving the supplied plan's field names.

User authorized defining the absent schema. These do not replace Core models and
must be reviewed by members 1/3 before real detector integration.
"""

from typing import Annotated, Any, Literal

from pydantic import Field

from ra_agent.contracts.common import ContractModel, UTCDateTime

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(min_length=1)]


class IntentSpec(ContractModel):
    intent_id: Identifier
    task_id: Identifier
    goal: Identifier
    scope: list[str]
    allowed_actions: list[str]
    forbidden_actions: list[str]
    success_criteria: list[str]
    source_refs: list[str]
    version: int = Field(ge=1)
    confirmed_by: str | None
    confirmed_at: UTCDateTime | None


class TaskContract(ContractModel):
    contract_id: Identifier
    contract_version: int = Field(ge=1)
    intent_ref: Identifier
    resources: list[str]
    tools: list[str]
    permissions: dict[str, str]
    completion_conditions: list[str]
    parent_version: int | None = Field(ge=1)
    status: Literal["DRAFT", "CONFIRMED", "SUPERSEDED"]
    digest: Digest


class BehaviorEvent(ContractModel):
    event_id: Identifier
    task_id: Identifier
    step_index: int = Field(ge=1)
    actor: Identifier
    source_type: Literal["user", "agent", "retrieval", "tool", "memory", "system"]
    source_ref: Identifier
    subgoal: str
    tool: Identifier
    action: Identifier
    args_digest: Digest
    target: Identifier
    risk_flags: list[str]
    timestamp: UTCDateTime


class DecisionResult(ContractModel):
    decision_id: Identifier
    decision: Literal["ALLOW", "BLOCK", "CLARIFY", "REPLAN"]
    risk_score: float = Field(ge=0, le=1)
    trigger_dimensions: list[str]
    evidence_refs: list[str]
    reason_code: Identifier
    policy_version: Identifier
    detector_version: Identifier
    expires_at: UTCDateTime


class CorrectionPlan(ContractModel):
    plan_id: Identifier
    base_contract_version: int = Field(ge=1)
    contaminated_refs: list[str]
    proposed_actions: list[dict[str, Any]]
    added_scope: list[str]
    confirmation_required: bool
    retry_budget: int = Field(ge=0, le=3)
    status: Literal["PROPOSED", "EXECUTED", "REJECTED"]


class EffectCheck(ContractModel):
    effect_id: Identifier
    request_id: Identifier
    tool: Identifier
    normalized_target: Identifier
    before_digest: Digest
    after_digest: Digest
    side_effect_ref: str | None
    status: Literal["UNCHANGED", "APPLIED", "FAILED", "MISMATCH"]


class IntentDemoSchema(ContractModel):
    """Catalog used to publish JSON Schema; never a production runtime envelope."""

    intent_spec: IntentSpec
    task_contract: TaskContract
    behavior_event: BehaviorEvent
    decision_result: DecisionResult
    correction_plan: CorrectionPlan
    effect_check: EffectCheck
