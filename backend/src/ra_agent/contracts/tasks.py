from pydantic import Field

from .boundary import TaskContract
from .common import ContractModel, UTCDateTime
from .enums import PermissionType, StepStatus


class TaskCreateRequest(ContractModel):
    objective: str = Field(min_length=1, description="User-visible task objective")
    contract: TaskContract | None = None


class TaskStep(ContractModel):
    task_id: str
    step_id: str
    description: str
    tool_name: str = ""
    arguments: dict[str, object] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    required_permissions: list[PermissionType] = Field(default_factory=list)
    status: StepStatus = StepStatus.PLANNED


class TaskResponse(ContractModel):
    task_id: str
    objective: str
    status: str
    created_at: UTCDateTime
    steps: list[TaskStep] = Field(default_factory=list)
