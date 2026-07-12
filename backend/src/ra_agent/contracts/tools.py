from typing import Any

from pydantic import Field

from .common import ContractModel, UTCDateTime
from .enums import PermissionType, RecoverabilityType, RiskLevel, SourceType


class ToolSpec(ContractModel):
    name: str
    description: str
    required_permissions: list[PermissionType] = Field(default_factory=list)
    base_risk: RiskLevel
    side_effect_type: str
    reversibility: RecoverabilityType
    sandbox_mode: str
    timeout_seconds: int = Field(gt=0)
    network_required: bool
    allowed_paths: list[str] = Field(default_factory=list)
    supports_dry_run: bool


class ToolCallRequest(ContractModel):
    task_id: str
    step_id: str
    request_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    objective: str = Field(min_length=1)
    context_summary: str = Field(min_length=1)
    source_type: SourceType
    requested_at: UTCDateTime
