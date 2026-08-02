from __future__ import annotations

import pytest
from ra_agent.core.bootstrap import build_mock_container
from ra_agent.core.providers import ExecutionProvider, ProviderRegistry, ServiceProvider
from ra_agent.execution.cleanup import RequestCleanupCoordinator
from ra_agent.execution.download_manager import DownloadLifecycleManager
from ra_agent.execution.effect_manager import EffectManager
from ra_agent.execution.effect_store import FilesystemEffectStore
from ra_agent.execution.execution_provider import (
    EXECUTION_PROVIDER_NAME,
    ExecutionProviderServices,
    ExecutionServiceProvider,
)
from ra_agent.execution.quarantine import FilesystemQuarantineStore
from ra_agent.execution.selective_rollback import SelectiveRollbackExecutor
from ra_agent.memory import FilesystemMemoryStore, MemoryLifecycleManager


def _provider(tmp_path) -> ExecutionServiceProvider:
    container = build_mock_container()
    effect_store = FilesystemEffectStore(tmp_path / "effects")
    effect_manager = EffectManager(effect_store)
    memory_manager = MemoryLifecycleManager(
        FilesystemMemoryStore(tmp_path / "memory", max_value_bytes=4096),
        effect_manager,
    )
    download_manager = DownloadLifecycleManager(
        FilesystemQuarantineStore(tmp_path / "quarantine", max_download_bytes=4096),
        effect_manager,
    )
    cleanup_coordinator = RequestCleanupCoordinator(
        rollback_manager=container.rollback_manager,
        memory_manager=memory_manager,
        download_manager=download_manager,
        effect_manager=effect_manager,
    )
    selective_rollback = SelectiveRollbackExecutor(
        effect_store=effect_store,
        effect_manager=effect_manager,
        rollback_manager=container.rollback_manager,
        memory_manager=memory_manager,
        download_manager=download_manager,
    )
    return ExecutionServiceProvider(
        ExecutionProviderServices(
            tool_executor=container.tool_executor,
            checkpoint_manager=container.checkpoint_manager,
            commit_gate=container.commit_gate,
            rollback_manager=container.rollback_manager,
            cleanup_coordinator=cleanup_coordinator,
            effect_store=effect_store,
            effect_manager=effect_manager,
            selective_rollback=selective_rollback,
            memory_manager=memory_manager,
            download_manager=download_manager,
        )
    )


def test_execution_provider_satisfies_frozen_provider_contract(tmp_path) -> None:
    provider = _provider(tmp_path)
    typed_provider: ExecutionProvider = provider

    assert typed_provider is provider
    assert isinstance(provider, ServiceProvider)
    assert provider.name == EXECUTION_PROVIDER_NAME


def test_execution_provider_registers_with_stable_name(tmp_path) -> None:
    provider = _provider(tmp_path)
    registry = ProviderRegistry()

    registration = registry.register(provider)

    assert registration.name == EXECUTION_PROVIDER_NAME
    assert registration.provider is provider
    assert registry.registrations() == (registration,)


def test_execution_provider_installs_only_execution_slots(tmp_path) -> None:
    original = build_mock_container()
    provider = _provider(tmp_path)
    installed = provider.install(original)
    services = provider.services

    assert installed is not original
    assert installed.tool_executor is services.tool_executor
    assert installed.checkpoint_manager is services.checkpoint_manager
    assert installed.commit_gate is services.commit_gate
    assert installed.rollback_manager is services.rollback_manager
    assert installed.cleanup_coordinator is services.cleanup_coordinator

    assert installed.risk_classifier is original.risk_classifier
    assert installed.policy_engine is original.policy_engine
    assert installed.permission_gate is original.permission_gate
    assert installed.audit_recorder is original.audit_recorder
    assert installed.approval_service is original.approval_service
    assert installed.tool_registry is original.tool_registry
    assert installed.request_registry is original.request_registry


def test_execution_provider_preserves_original_container(tmp_path) -> None:
    original = build_mock_container()
    original_cleanup = original.cleanup_coordinator
    provider = _provider(tmp_path)

    provider.install(original)

    assert original.cleanup_coordinator is original_cleanup


def test_execution_provider_retains_shared_member_services(tmp_path) -> None:
    provider = _provider(tmp_path)
    services = provider.services

    assert services.effect_manager.store is services.effect_store
    assert services.selective_rollback is provider.services.selective_rollback
    assert services.memory_manager is provider.services.memory_manager
    assert services.download_manager is provider.services.download_manager


def test_execution_provider_rejects_mismatched_effect_store(tmp_path) -> None:
    container = build_mock_container()
    effect_store = FilesystemEffectStore(tmp_path / "effects-a")
    other_store = FilesystemEffectStore(tmp_path / "effects-b")
    effect_manager = EffectManager(other_store)
    memory_manager = MemoryLifecycleManager(
        FilesystemMemoryStore(tmp_path / "memory", max_value_bytes=4096),
        effect_manager,
    )
    download_manager = DownloadLifecycleManager(
        FilesystemQuarantineStore(tmp_path / "quarantine", max_download_bytes=4096),
        effect_manager,
    )
    cleanup_coordinator = RequestCleanupCoordinator(
        rollback_manager=container.rollback_manager,
        memory_manager=memory_manager,
        download_manager=download_manager,
        effect_manager=effect_manager,
    )
    selective_rollback = SelectiveRollbackExecutor(
        effect_store=other_store,
        effect_manager=effect_manager,
        rollback_manager=container.rollback_manager,
        memory_manager=memory_manager,
        download_manager=download_manager,
    )

    with pytest.raises(ValueError, match="provider effect_store"):
        ExecutionProviderServices(
            tool_executor=container.tool_executor,
            checkpoint_manager=container.checkpoint_manager,
            commit_gate=container.commit_gate,
            rollback_manager=container.rollback_manager,
            cleanup_coordinator=cleanup_coordinator,
            effect_store=effect_store,
            effect_manager=effect_manager,
            selective_rollback=selective_rollback,
            memory_manager=memory_manager,
            download_manager=download_manager,
        )
