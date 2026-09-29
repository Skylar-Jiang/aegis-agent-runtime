from pydantic import Field

from .common import ContractModel


class TaskContract(ContractModel):
    """User-approved bounds for every tool call belonging to a task."""

    allowed_actions: list[str] = Field(min_length=1)
    allowed_resources: list[str] = Field(min_length=1)
    forbidden_actions: list[str] = Field(default_factory=list)
    max_affected_objects: int = Field(gt=0)
    allow_egress: bool = False
    approval_required_actions: list[str] = Field(default_factory=list)
    bulk_approval_threshold: int = Field(default=20, gt=0)
    security_profile_id: str | None = None
    security_profile_version: int | None = Field(default=None, gt=0)
    # Kept only so old saved tasks and Demo fixtures remain readable. New workbench
    # requests use approval_required_actions and the Runtime approval flow.
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
