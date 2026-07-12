from typing import Any

from pydantic import Field

from .common import ContractModel, UTCDateTime
from .enums import AuditEventType, PolicyDecision, RiskLevel


class AuditEvent(ContractModel):
    event_id: str
    task_id: str
    step_id: str | None = None
    request_id: str | None = None
    sequence_number: int = Field(gt=0)
    event_type: AuditEventType
    timestamp: UTCDateTime
    actor: str
    status: str
    risk_level: RiskLevel | None = None
    decision: PolicyDecision | None = None
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
