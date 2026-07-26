from dataclasses import dataclass, field
from enum import StrEnum

from ra_agent.contracts import ToolCallRequest, ToolExecutionResult


class AgentRunStatus(StrEnum):
    PLANNING = "PLANNING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass(slots=True)
class AgentState:
    task_id: str
    objective: str
    planned_requests: list[ToolCallRequest] = field(default_factory=list)
    results: list[ToolExecutionResult] = field(default_factory=list)
    status: AgentRunStatus = AgentRunStatus.PLANNING
    failure_code: str | None = None
    failure_reason: str | None = None
