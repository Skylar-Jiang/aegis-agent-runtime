from pydantic import Field

from .common import ContractModel
from .enums import PermissionStatus, PermissionType, PolicyDecision, RiskLevel


class RiskVerdict(ContractModel):
    request_id: str
    risk_level: RiskLevel
    recommended_decision: PolicyDecision
    reason: str
    signals: list[str] = Field(default_factory=list)


class PermissionDecision(ContractModel):
    request_id: str
    permission: PermissionType
    status: PermissionStatus
    reason: str
