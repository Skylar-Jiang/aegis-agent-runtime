from typing import Any

from pydantic import AwareDatetime, Field

from .common import ContractModel
from .enums import PermissionType, RecoverabilityType, RiskLevel


class ToolSpec(ContractModel):
    name: str
    description: str
    required_permissions: list[PermissionType]
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
    arguments: dict[str, Any]
    requested_at: AwareDatetime
