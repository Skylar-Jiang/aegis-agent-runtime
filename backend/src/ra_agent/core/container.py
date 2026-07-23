from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from ra_agent.audit import AuditRecorder
from ra_agent.execution import CheckpointManager, CommitGate, RollbackManager, ToolExecutor
from ra_agent.execution.cleanup import RequestCleanupCoordinator
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
