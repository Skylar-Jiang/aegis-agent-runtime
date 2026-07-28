from __future__ import annotations

from dataclasses import dataclass, replace

from ra_agent.core.container import ServiceContainer
from ra_agent.core.providers import ExecutionProvider
from ra_agent.memory import MemoryLifecycleManager

from .checkpoint import CheckpointManager
from .cleanup import RequestCleanupCoordinator
from .commit_gate import CommitGate
from .download_manager import DownloadLifecycleManager
from .effect_manager import EffectManager
from .effect_store import FilesystemEffectStore
from .executor import ToolExecutor
from .rollback import RollbackManager
from .selective_rollback import SelectiveRollbackExecutor

EXECUTION_PROVIDER_NAME = "execution.v2-effects-rollback"


@dataclass(frozen=True, slots=True)
class ExecutionProviderServices:
    """Member-owned execution services installed through the frozen provider contract.

    The frozen ``ServiceContainer`` does not yet expose slots for effect queries or
    graph-level rollback. Those services remain available through this concrete bundle
    so the group lead can wire them in a later owner-only integration change without
    the provider redefining a public Protocol or Contract.
    """

    tool_executor: ToolExecutor
    checkpoint_manager: CheckpointManager
    commit_gate: CommitGate
    rollback_manager: RollbackManager
    cleanup_coordinator: RequestCleanupCoordinator
    effect_store: FilesystemEffectStore
    effect_manager: EffectManager
    selective_rollback: SelectiveRollbackExecutor
    memory_manager: MemoryLifecycleManager
    download_manager: DownloadLifecycleManager

    def __post_init__(self) -> None:
        if self.effect_manager.store is not self.effect_store:
            raise ValueError("effect_manager must use the provider effect_store")


class ExecutionServiceProvider(ExecutionProvider):
    """Install member 3 execution services without modifying bootstrap or Runtime."""

    name = EXECUTION_PROVIDER_NAME

    def __init__(self, services: ExecutionProviderServices) -> None:
        self._services = services

    @property
    def services(self) -> ExecutionProviderServices:
        """Return the exact shared service bundle retained by this provider."""

        return self._services

    def install(self, container: ServiceContainer) -> ServiceContainer:
        """Return a new container with only member 3 execution slots replaced."""

        services = self._services
        return replace(
            container,
            tool_executor=services.tool_executor,
            checkpoint_manager=services.checkpoint_manager,
            commit_gate=services.commit_gate,
            rollback_manager=services.rollback_manager,
            cleanup_coordinator=services.cleanup_coordinator,
        )
