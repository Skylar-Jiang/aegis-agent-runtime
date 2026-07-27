from dataclasses import replace

import pytest

from ra_agent.core.container import ServiceContainer
from ra_agent.core.providers import ProviderRegistry, ServiceProvider


class StubProvider:
    name = "security.stub"

    def install(self, container: ServiceContainer) -> ServiceContainer:
        return replace(container)


def test_provider_registry_registers_stable_provider_contract() -> None:
    provider = StubProvider()
    registry = ProviderRegistry()

    registration = registry.register(provider)

    assert isinstance(provider, ServiceProvider)
    assert registration.name == provider.name
    assert registry.registrations() == (registration,)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(provider)
