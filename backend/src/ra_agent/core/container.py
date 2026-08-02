from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from ra_agent.audit import AuditRecorder
from ra_agent.execution import CheckpointManager, CommitGate, RollbackManager, ToolExecutor
from ra_agent.execution.cleanup import RequestCleanupCoordinator
from ra_agent.execution.download_manager import DownloadLifecycleManager
from ra_agent.execution.effect_manager import EffectManager
from ra_agent.execution.effect_store import FilesystemEffectStore
from ra_agent.execution.selective_rollback import SelectiveRollbackExecutor
from ra_agent.memory import MemoryLifecycleManager
from ra_agent.runtime.idempotency import RequestExecutionRegistry
from ra_agent.security import (
    ApprovalService,
    DeepSafetyChecker,
    IntentBoundaryGuard,
    PermissionGate,
    PolicyEngine,
    PostExecutionChecker,
    PreExecutionChecker,
    RiskClassifier,
)
from ra_agent.tools import ToolRegistry


@dataclass(frozen=True, slots=True)
class ServiceContainer:
    risk_classifier: RiskClassifier
    policy_engine: PolicyEngine
    permission_gate: PermissionGate
    tool_executor: ToolExecutor
    deep_safety_checker: DeepSafetyChecker
    pre_execution_checker: PreExecutionChecker
    post_execution_checker: PostExecutionChecker
    checkpoint_manager: CheckpointManager
    commit_gate: CommitGate
    rollback_manager: RollbackManager
    approval_service: ApprovalService
    audit_recorder: AuditRecorder
    tool_registry: ToolRegistry
    request_registry: RequestExecutionRegistry
    cleanup_coordinator: RequestCleanupCoordinator | None = None
    database_engine: AsyncEngine | None = None
    intent_boundary_guard: IntentBoundaryGuard | None = None
    execution_monitor: PreExecutionChecker | None = None
    effect_store: FilesystemEffectStore | None = None
    effect_manager: EffectManager | None = None
    memory_manager: MemoryLifecycleManager | None = None
    download_manager: DownloadLifecycleManager | None = None
    selective_rollback_executor: SelectiveRollbackExecutor | None = None
