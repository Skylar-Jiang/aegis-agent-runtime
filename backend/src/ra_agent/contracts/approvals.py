from .common import ContractModel, UTCDateTime


class ApprovalRequest(ContractModel):
    approval_id: str
    task_id: str
    step_id: str
    request_id: str
    reason: str
    requested_at: UTCDateTime
    expires_at: UTCDateTime


class ApprovalDecision(ContractModel):
    approval_id: str
    granted: bool
    decided_by: str
    decided_at: UTCDateTime
    reason: str
