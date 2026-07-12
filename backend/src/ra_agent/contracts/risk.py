from pydantic import Field

from .common import ContractModel
from .enums import PermissionStatus, PermissionType, PolicyDecision, RiskLevel


class RiskVerdict(ContractModel):
    request_id: str
    risk_level: RiskLevel
    recommended_decision: PolicyDecision
    reason: str
    signals: list[str] = Field(default_factory=list)
    matched_rules: list[str] = Field(default_factory=list)
    requires_deep_check: bool = False
    requires_checkpoint: bool = False


class PermissionDecision(ContractModel):
    request_id: str
    permission: PermissionType
    status: PermissionStatus
    reason: str


class PermissionCheckResult(ContractModel):
    request_id: str
    decisions: list[PermissionDecision] = Field(default_factory=list)
    allowed: bool
    requires_approval: bool
    reason: str
