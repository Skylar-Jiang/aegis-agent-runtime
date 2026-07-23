from pydantic import Field

from .common import ContractModel


class TaskContract(ContractModel):
    """User-approved bounds for every tool call belonging to a task."""

    allowed_actions: list[str] = Field(min_length=1)
    allowed_resources: list[str] = Field(min_length=1)
    forbidden_actions: list[str] = Field(default_factory=list)
    max_affected_objects: int = Field(gt=0)
    allow_egress: bool = False
    requires_reconfirmation: bool = False


class IntentBoundaryResult(ContractModel):
    allowed: bool
    reason: str
    signals: list[str] = Field(default_factory=list)


class DataLineage(ContractModel):
    artifact_id: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    sensitivity: str = Field(pattern="^(PUBLIC|INTERNAL|CONFIDENTIAL|SECRET)$")
    source: str = Field(min_length=1)
    allowed_recipients: list[str] = Field(default_factory=list)


class PendingEgress(ContractModel):
    status: str = "PENDING_EGRESS"
    artifact: DataLineage
    recipient: str = Field(min_length=1)
