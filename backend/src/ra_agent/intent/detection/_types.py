"""Private detector inputs; these do not define or replace the frozen Intent API.

Only a trusted runtime adapter may construct these snapshots. Source contents and
Agent explanations are data, never authorization or verified execution progress.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class Disposition(StrEnum):
    CONTINUE = "CONTINUE"
    REPLAN = "REPLAN"
    REQUEST_CONFIRMATION = "REQUEST_CONFIRMATION"
    BLOCK = "BLOCK"
    SAFE_TERMINATE = "SAFE_TERMINATE"


@dataclass(frozen=True, slots=True)
class ContractSnapshot:
    task_id: str
    contract_id: str
    version: str
    goal: str
    evidence_ref: str
    confirmed: bool = True
    allowed_tools: tuple[str, ...] | None = None
    allowed_actions: tuple[str, ...] | None = None
    forbidden_actions: tuple[str, ...] = ()
    resource_limits: tuple[str, ...] | None = None
    goal_targets: tuple[str, ...] = ()
    authorized_recipients: tuple[str, ...] = ()
    allow_egress: bool = False
    success_criteria: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateAction:
    request_id: str
    step_index: int
    tool: str
    action: str
    target: str
    evidence_ref: str
    subgoal: str = ""
    parameters_text: str = ""
    source_refs: tuple[str, ...] = ()
    effect: str = "none"
    recipient: str | None = None
    # A verified dependency can justify low lexical overlap for an intermediate step.
    verified_support: bool = False
    additional_targets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceRecord:
    ref: str
    source_type: str
    content: str = ""
    parent_refs: tuple[str, ...] = ()
    # A runtime-verified tool status is separate from prose claiming tool failure.
    verified_tool_failure: bool = False


@dataclass(frozen=True, slots=True)
class ObservedBehavior:
    event_id: str
    task_id: str
    contract_version: str
    step_index: int
    tool: str
    action: str
    target: str
    evidence_ref: str
    status: str = "succeeded"
    subgoal: str = ""
    source_refs: tuple[str, ...] = ()
    effect: str = "none"
    verified_progress: bool | None = None
    verified_support: bool = False


@dataclass(frozen=True, slots=True)
class DetectionInput:
    contract: ContractSnapshot
    candidate: CandidateAction
    history: tuple[ObservedBehavior, ...] = ()
    sources: tuple[SourceRecord, ...] = ()
    # These are trusted facts from the execution/permission chain, not model claims.
    hard_violations: tuple[str, ...] = ()
    replan_attempts: int = 0
    repeated_recovery: bool = False
    recovery_budget_exhausted: bool = False
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    def validate(self) -> None:
        flags = (
            self.contract.confirmed,
            self.contract.allow_egress,
            self.candidate.verified_support,
            self.repeated_recovery,
            self.recovery_budget_exhausted,
        )
        if any(type(flag) is not bool for flag in flags):
            raise ValueError("authorization and support flags must be strict booleans")
        if not all(
            (
                self.contract.task_id,
                self.contract.contract_id,
                self.contract.version,
                self.contract.goal,
                self.contract.evidence_ref,
                self.candidate.request_id,
                self.candidate.tool,
                self.candidate.action,
                self.candidate.evidence_ref,
            )
        ):
            raise ValueError("runtime snapshot is missing required task/action evidence")
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("detection time must include a timezone")
        if type(self.candidate.step_index) is not int or self.candidate.step_index < 0:
            raise ValueError("step index must be a nonnegative integer")
        if type(self.replan_attempts) is not int or self.replan_attempts < 0:
            raise ValueError("replan attempts must be a nonnegative trusted counter")
        if self.candidate.effect not in {"none", "write", "delete", "send", "execute"}:
            raise ValueError("unrecognized candidate effect")
        identifiers = (
            self.contract.task_id,
            self.contract.contract_id,
            self.contract.version,
            self.candidate.request_id,
        )
        if any(not isinstance(value, str) or len(value) > 128 for value in identifiers):
            raise ValueError("identifiers must be strings of at most 128 characters")
        if any(
            len(value) > 512 for value in (self.contract.evidence_ref, self.candidate.evidence_ref)
        ):
            raise ValueError("evidence references exceed the supported length")
        if len(self.contract.goal) > 65536:
            raise ValueError("task goal exceeds the supported input budget")
        collections = (
            self.contract.allowed_tools or (),
            self.contract.allowed_actions or (),
            self.contract.forbidden_actions,
            self.contract.resource_limits or (),
            self.contract.goal_targets,
            self.contract.authorized_recipients,
            self.contract.success_criteria,
            self.candidate.additional_targets,
        )
        if any(
            len(values) > 256 or any(len(value) > 4096 for value in values)
            for values in collections
        ):
            raise ValueError("constraint collection exceeds the supported input budget")
        for event in self.history:
            if (
                event.task_id != self.contract.task_id
                or event.contract_version != self.contract.version
                or event.step_index >= self.candidate.step_index
            ):
                continue
            if type(event.verified_support) is not bool or (
                event.verified_progress is not None and type(event.verified_progress) is not bool
            ):
                raise ValueError("historical support/progress must be verified boolean facts")


@dataclass(frozen=True, slots=True)
class Assessment:
    disposition: Disposition
    risk_score: float
    trigger_dimensions: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    reason_code: str
    reasons: tuple[str, ...]
    policy_version: str
    detector_version: str
    expires_at: datetime
    features: tuple[tuple[str, float], ...] = ()
    contaminated_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(self.risk_score) or not 0 <= self.risk_score <= 1:
            raise ValueError("risk score must be finite and between zero and one")


@dataclass(frozen=True, slots=True)
class RecoveryContext:
    """Structured facts for the planner; never includes untrusted source prose."""

    task_id: str
    contract_id: str
    contract_version: str
    goal: str
    allowed_tools: tuple[str, ...] | None
    allowed_actions: tuple[str, ...] | None
    resource_limits: tuple[str, ...] | None
    forbidden_actions: tuple[str, ...]
    success_criteria: tuple[str, ...]
    verified_progress_refs: tuple[str, ...]
    excluded_source_refs: tuple[str, ...]
    reason_codes: tuple[str, ...]
    remaining_attempts: int
