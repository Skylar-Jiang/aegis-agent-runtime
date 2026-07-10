from typing import TypedDict


class AgentState(TypedDict):
    task_id: str
    objective: str
    planned_steps: list[str]
