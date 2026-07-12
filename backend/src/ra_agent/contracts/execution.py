from typing import Any

from pydantic import Field

from .common import ContractModel, UTCDateTime
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
    error_code: str | None = None
    checkpoint_id: str | None = None
    sandbox_path: str | None = None
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    pending_changes: list[dict[str, Any]] = Field(default_factory=list)
    started_at: UTCDateTime | None = None
    finished_at: UTCDateTime | None = None


class DeepCheckResult(ContractModel):
    request_id: str
    passed: bool
    reason: str
    signals: list[str] = Field(default_factory=list)


class CommitResult(ContractModel):
    request_id: str
    checkpoint_id: str | None = None
    status: ExecutionStatus


class RollbackResult(ContractModel):
    request_id: str
    checkpoint_id: str
    status: ExecutionStatus
    reason: str
