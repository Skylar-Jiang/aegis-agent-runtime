from .planner import Planner
from .runtime import AgentRuntime, RuntimeSchedulerPort


def build_graph(*, planner: Planner, scheduler: RuntimeSchedulerPort) -> AgentRuntime:
    """Return the minimal state flow; safety execution remains inside RuntimeScheduler."""

    return AgentRuntime(planner=planner, scheduler=scheduler)
