from pydantic import AwareDatetime

from .common import ContractModel


class ApprovalRequest(ContractModel):
    approval_id: str
    task_id: str
    step_id: str
    request_id: str
    reason: str
    requested_at: AwareDatetime
    expires_at: AwareDatetime


class ApprovalDecision(ContractModel):
    approval_id: str
    granted: bool
    decided_by: str
    decided_at: AwareDatetime
    reason: str
