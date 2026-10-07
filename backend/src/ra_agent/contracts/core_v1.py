"""Aegis Core v1 public contracts used by both Cap and Intent branches.

These models intentionally keep the public field names from the one-week Core plan.
Internal implementation variables may differ, but anything crossing module/API boundaries
should use these names and types.
"""

from __future__ import annotations

import math
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from .common import ContractModel, UTCDateTime


class GrantScope(StrEnum):
    GLOBAL = "GLOBAL"
    SESSION = "SESSION"
    TASK = "TASK"
    ONE_SHOT = "ONE_SHOT"


class GrantEffect(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class ContractVersionStatus(StrEnum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    SUPERSEDED = "SUPERSEDED"


class GatewayDecisionType(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"
    REQUIRE_REPLAN = "REQUIRE_REPLAN"




class ConfirmationStatus(StrEnum):
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


class GatewayReasonCode(StrEnum):
    ALLOWED = "ALLOWED"
    USER_DENY = "USER_DENY"
    OUT_OF_CONTRACT = "OUT_OF_CONTRACT"
    SKILL_LIMIT = "SKILL_LIMIT"
    SYSTEM_DENY = "SYSTEM_DENY"
    RESOURCE_MISMATCH = "RESOURCE_MISMATCH"
    VERSION_STALE = "VERSION_STALE"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    CHECK_UNAVAILABLE = "CHECK_UNAVAILABLE"
    ADAPTER_BYPASS = "ADAPTER_BYPASS"
    SIGNATURE_INVALID = "SIGNATURE_INVALID"
    INTENT_DRIFT = "INTENT_DRIFT"
    INTENT_REPLAN_REQUIRED = "INTENT_REPLAN_REQUIRED"
    INTENT_CONFIRMATION_REQUIRED = "INTENT_CONFIRMATION_REQUIRED"
    INTENT_CHECK_TIMEOUT = "INTENT_CHECK_TIMEOUT"
    INTENT_CHECK_UNAVAILABLE = "INTENT_CHECK_UNAVAILABLE"
    INTENT_CONTRACT_VERSION_MISMATCH = "INTENT_CONTRACT_VERSION_MISMATCH"
    INTENT_SAFE_TERMINATE = "INTENT_SAFE_TERMINATE"


class EffectClass(StrEnum):
    READ = "READ"
    WRITE = "WRITE"
    DELETE = "DELETE"
    MEMORY = "MEMORY"
    NETWORK = "NETWORK"
    PROCESS = "PROCESS"
    OTHER = "OTHER"


class ContractPermissionRule(ContractModel):
    """One TaskContractV2 allow/deny boundary rule.

    ``*`` is supported for tool/action/effect. ``resource`` additionally accepts
    fnmatch-style patterns such as ``reports/**``.
    """

    tool: str = "*"
    action: str = Field(min_length=1)
    resource: str = "*"
    effect: str = "*"


class ConfirmationPolicyV1(ContractModel):
    required_actions: list[str] = Field(default_factory=list)
    required_effect_classes: list[EffectClass] = Field(default_factory=list)


class TaskContractV2(ContractModel):
    contract_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    goals: list[str] = Field(min_length=1)
    completion_criteria: list[str] = Field(default_factory=list)
    allowed: list[ContractPermissionRule] = Field(default_factory=list)
    denied: list[ContractPermissionRule] = Field(default_factory=list)
    limits: dict[str, int | float | str | bool] = Field(default_factory=dict)
    confirmation: ConfirmationPolicyV1 = Field(default_factory=ConfirmationPolicyV1)
    policy_version: str = Field(min_length=1)
    tool_manifest_digest: str = Field(min_length=1)
    parent_digest: str | None = None

    # Reserved Core extension points. Core serializes these fields but does not
    # interpret branch-specific semantics.
    prepared_effect_id: str | None = None
    effect_descriptor_digest: str | None = None
    result_commitment: str | None = None
    commit_epoch: int | None = Field(default=None, ge=0)
    execution_receipt_id: str | None = None
    compliance_proof_ref: str | None = None


class ContractVersionRef(ContractModel):
    contract_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    digest: str = Field(min_length=1)
    status: ContractVersionStatus
    confirmed_by: str | None = None
    confirmed_at: UTCDateTime | None = None


class ConfirmationRecord(ContractModel):
    confirmation_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    contract_ref: ContractVersionRef
    policy_version: str = Field(min_length=1)
    status: ConfirmationStatus
    requested_at: UTCDateTime
    resolved_at: UTCDateTime | None = None
    resolved_by: str | None = None


class ConfirmationResolveRequest(ContractModel):
    confirmed: bool
    resolved_by: str = Field(default="user", min_length=1)


class PermissionGrant(ContractModel):
    subject: str = Field(min_length=1)
    skill: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    action: str = Field(min_length=1)
    resource: str = Field(min_length=1)
    effect: GrantEffect
    scope: GrantScope
    expires_at: UTCDateTime | None = None
    source: str = Field(min_length=1)
    # Optional numeric ceilings contributed by this authorization layer.
    # EffectivePermission keeps the smallest value seen for each shared key.
    limits: dict[str, int | float] = Field(default_factory=dict)
    # Required to bind SESSION/TASK/ONE_SHOT grants to their concrete context.
    # GLOBAL grants leave this as None.
    scope_ref: str | None = None

    @field_validator("limits", mode="before")
    @classmethod
    def validate_limits(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        for name, limit in value.items():
            if not isinstance(name, str) or not name:
                raise ValueError("permission limit names must be non-empty strings")
            if (
                isinstance(limit, bool)
                or not isinstance(limit, (int, float))
                or limit < 0
                or not math.isfinite(float(limit))
            ):
                raise ValueError(f"permission limit {name!r} must be a finite non-negative number")
        return value

    @model_validator(mode="after")
    def validate_scope_ref(self) -> PermissionGrant:
        if self.scope is GrantScope.GLOBAL and self.scope_ref is not None:
            raise ValueError("GLOBAL grants must not set scope_ref")
        if self.scope is not GrantScope.GLOBAL and not self.scope_ref:
            raise ValueError(f"{self.scope.value} grants require scope_ref")
        return self


class EffectivePermission(ContractModel):
    allowed: list[PermissionGrant] = Field(default_factory=list)
    denied: list[PermissionGrant] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    matched_sources: list[str] = Field(default_factory=list)
    conflict_reason: str | None = None
    # Additional machine-readable fields used by ToolGateway. The plan's core
    # fields above remain unchanged.
    conflict_code: GatewayReasonCode | None = None
    requires_confirmation: bool = False


class ToolCallEnvelope(ContractModel):
    request_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    contract_ref: ContractVersionRef
    skill_ref: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    action: str = Field(min_length=1)
    canonical_args: dict[str, Any] = Field(default_factory=dict)
    resource: str = Field(min_length=1)
    effect_class: EffectClass


class GatewayDecision(ContractModel):
    decision: GatewayDecisionType
    reason_code: GatewayReasonCode
    evidence_refs: list[str] = Field(default_factory=list)
    versions: dict[str, int | str] = Field(default_factory=dict)
    confirmation_id: str | None = None


class PermissionContext(ContractModel):
    user_grants: list[PermissionGrant] = Field(default_factory=list)
    skill_grants: list[PermissionGrant] = Field(default_factory=list)
    system_grants: list[PermissionGrant] = Field(default_factory=list)


class ContractRecord(ContractModel):
    contract: TaskContractV2
    ref: ContractVersionRef


class TaskContractCreateRequest(ContractModel):
    session_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    goals: list[str] = Field(min_length=1)
    completion_criteria: list[str] = Field(default_factory=list)
    allowed: list[ContractPermissionRule] = Field(default_factory=list)
    denied: list[ContractPermissionRule] = Field(default_factory=list)
    limits: dict[str, int | float | str | bool] = Field(default_factory=dict)
    confirmation: ConfirmationPolicyV1 = Field(default_factory=ConfirmationPolicyV1)
    policy_version: str = Field(min_length=1)
    tool_manifest_digest: str = Field(min_length=1)


class TaskContractUpdateRequest(ContractModel):
    goals: list[str] | None = None
    completion_criteria: list[str] | None = None
    allowed: list[ContractPermissionRule] | None = None
    denied: list[ContractPermissionRule] | None = None
    limits: dict[str, int | float | str | bool] | None = None
    confirmation: ConfirmationPolicyV1 | None = None
    policy_version: str | None = None
    tool_manifest_digest: str | None = None


class ToolReplanRequest(ContractModel):
    changes: TaskContractUpdateRequest


class ContractConfirmRequest(ContractModel):
    version: int = Field(ge=1)
    confirmed_by: str = Field(min_length=1)


class SessionCreateRequestV1(ContractModel):
    user_id: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=200)
    security_profile_id: str = "default"


class SessionViewV1(ContractModel):
    session_id: str
    user_id: str
    title: str
    security_profile_id: str
    created_at: UTCDateTime


class SessionTaskCreateRequestV1(ContractModel):
    objective: str = Field(min_length=1)
    completion_criteria: list[str] = Field(default_factory=list)


class SessionTaskDraftV1(ContractModel):
    task_id: str
    session_id: str
    status: str
    contract: ContractRecord


class ToolEvaluationRequest(ContractModel):
    envelope: ToolCallEnvelope
    permissions: PermissionContext


class GatewayEvaluationResult(ContractModel):
    decision: GatewayDecision
    effective_permission: EffectivePermission


class GatewayExecutionResult(ContractModel):
    request_id: str
    status: str
    result: dict[str, Any] = Field(default_factory=dict)
