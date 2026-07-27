from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .container import ServiceContainer


@runtime_checkable
class ServiceProvider(Protocol):
    """A member-owned dependency contribution, installed only by the group lead."""

    name: str

    def install(self, container: ServiceContainer) -> ServiceContainer: ...


class SecurityProvider(ServiceProvider, Protocol):
    pass


class ExecutionProvider(ServiceProvider, Protocol):
    pass


class AuditExperimentProvider(ServiceProvider, Protocol):
    pass


@dataclass(frozen=True, slots=True)
class ProviderRegistration:
    name: str
    provider: ServiceProvider


class ProviderRegistry:
    """Collect registrations without changing bootstrap or installing providers."""

    def __init__(self) -> None:
        self._registrations: dict[str, ProviderRegistration] = {}

    def register(self, provider: ServiceProvider) -> ProviderRegistration:
        if not provider.name.strip():
            raise ValueError("provider name must not be blank")
        if provider.name in self._registrations:
            raise ValueError(f"provider already registered: {provider.name}")
        registration = ProviderRegistration(name=provider.name, provider=provider)
        self._registrations[registration.name] = registration
        return registration

    def registrations(self) -> tuple[ProviderRegistration, ...]:
        return tuple(self._registrations.values())
