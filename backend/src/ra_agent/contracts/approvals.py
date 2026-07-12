from typing import Any

from pydantic import model_validator

from .common import ContractModel, UTCDateTime
from .enums import ApprovalStatus


class ApprovalRequest(ContractModel):
    approval_id: str
    task_id: str
    step_id: str
    request_id: str
    tool_name: str
    request_fingerprint: str
    reason: str
    requested_at: UTCDateTime
    expires_at: UTCDateTime
    status: ApprovalStatus = ApprovalStatus.PENDING


class ApprovalDecision(ContractModel):
    approval_id: str
    task_id: str
    step_id: str
    request_id: str
    status: ApprovalStatus
    decided_by: str
    decided_at: UTCDateTime
    reason: str
    granted: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _support_legacy_granted(cls, value: Any) -> Any:
        if isinstance(value, dict) and "status" not in value and "granted" in value:
            value = dict(value)
            value["status"] = ApprovalStatus.GRANTED if value["granted"] else ApprovalStatus.DENIED
        return value

    @model_validator(mode="after")
    def _validate_granted_consistency(self) -> "ApprovalDecision":
        if self.granted is not None and self.granted != (self.status is ApprovalStatus.GRANTED):
            raise ValueError("granted must agree with approval status")
        return self
