"""Additive public records for the bounded Intent rule detector."""

from pydantic import Field

from .common import ContractModel


class IntentAssessment(ContractModel):
    schema_version: str = "intent-rule-v1"
    risk_score: float = Field(ge=0, le=1)
    trigger_dimensions: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    policy_version: str
    detector_version: str = "bounded-report-rule:1"
    contract_version: int
    contract_digest: str
    step_index: int
    disposition: str
    reason: str
    enabled: bool
