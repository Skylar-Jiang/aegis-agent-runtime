from typing import Any

from .common import ContractModel
from .enums import ExecutionStatus


class CheckpointResult(ContractModel):
    task_id: str
    step_id: str
    request_id: str
    checkpoint_id: str
    status: ExecutionStatus


class ToolExecutionResult(ContractModel):
    task_id: str
    step_id: str
    request_id: str
    status: ExecutionStatus
    output: Any = None
    error: str | None = None


class DeepCheckResult(ContractModel):
    request_id: str
    passed: bool
    reason: str


class CommitResult(ContractModel):
    request_id: str
    checkpoint_id: str | None = None
    status: ExecutionStatus


class RollbackResult(ContractModel):
    request_id: str
    checkpoint_id: str
    status: ExecutionStatus
    reason: str
