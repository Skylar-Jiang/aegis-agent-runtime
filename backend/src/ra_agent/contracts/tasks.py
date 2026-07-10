from pydantic import AwareDatetime, Field

from .common import ContractModel
from .enums import StepStatus


class TaskCreateRequest(ContractModel):
    objective: str = Field(min_length=1, description="User-visible task objective")


class TaskStep(ContractModel):
    task_id: str
    step_id: str
    description: str
    status: StepStatus = StepStatus.PLANNED


class TaskResponse(ContractModel):
    task_id: str
    objective: str
    status: str
    created_at: AwareDatetime
    steps: list[TaskStep] = Field(default_factory=list)
