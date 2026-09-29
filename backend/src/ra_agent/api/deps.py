"""FastAPI dependency injection — provides typed access to ServiceContainer."""

from fastapi import Request

from ra_agent.agent import AgentRuntime
from ra_agent.core.container import ServiceContainer


def get_services(request: Request) -> ServiceContainer:
    return request.app.state.services


def get_agent_runner(request: Request) -> AgentRuntime:
    return request.app.state.agent_runner
