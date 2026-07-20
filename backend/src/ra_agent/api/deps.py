"""FastAPI dependency injection — provides typed access to ServiceContainer."""

from fastapi import Request

from ra_agent.core.container import ServiceContainer


def get_services(request: Request) -> ServiceContainer:
    return request.app.state.services
