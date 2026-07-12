from ra_agent.audit import InMemoryAuditRecorder
from ra_agent.execution import (
    MockCheckpointManager,
    MockCommitGate,
    MockRollbackManager,
    MockToolExecutor,
)
from ra_agent.runtime import InMemoryRequestExecutionRegistry, RuntimeScheduler
from ra_agent.runtime.approval_flow import ApprovalFlow
from ra_agent.runtime.fast_flow import FastExecutionFlow
from ra_agent.runtime.sandbox_flow import SandboxFlow
from ra_agent.security import (
    MockApprovalService,
    MockDeepSafetyChecker,
    MockPermissionGate,
    MockPolicyEngine,
    MockRiskClassifier,
)
from ra_agent.tools import DEFAULT_TOOL_SPECS, MockToolHandler, ToolRegistry

from .container import ServiceContainer


def build_mock_container() -> ServiceContainer:
    registry = ToolRegistry()
    for spec in DEFAULT_TOOL_SPECS:
        registry.register(spec.model_copy(deep=True), MockToolHandler())

    return ServiceContainer(
        risk_classifier=MockRiskClassifier(),
        policy_engine=MockPolicyEngine(),
        permission_gate=MockPermissionGate(),
        tool_executor=MockToolExecutor(),
        deep_safety_checker=MockDeepSafetyChecker(),
        checkpoint_manager=MockCheckpointManager(),
        commit_gate=MockCommitGate(),
        rollback_manager=MockRollbackManager(),
        approval_service=MockApprovalService(),
        audit_recorder=InMemoryAuditRecorder(),
        tool_registry=registry,
        request_registry=InMemoryRequestExecutionRegistry(),
    )


def build_runtime_scheduler(container: ServiceContainer) -> RuntimeScheduler:
    fast_flow = FastExecutionFlow(
        executor=container.tool_executor,
        audit_recorder=container.audit_recorder,
    )
    sandbox_flow = SandboxFlow(
        checkpoint_manager=container.checkpoint_manager,
        executor=container.tool_executor,
        deep_checker=container.deep_safety_checker,
        commit_gate=container.commit_gate,
        rollback_manager=container.rollback_manager,
        audit_recorder=container.audit_recorder,
    )
    approval_flow = ApprovalFlow(
        approval_service=container.approval_service,
        audit_recorder=container.audit_recorder,
        request_registry=container.request_registry,
        tool_registry=container.tool_registry,
        classifier=container.risk_classifier,
        policy=container.policy_engine,
        permission_gate=container.permission_gate,
        fast_flow=fast_flow,
        sandbox_flow=sandbox_flow,
    )
    return RuntimeScheduler(
        classifier=container.risk_classifier,
        policy=container.policy_engine,
        permission_gate=container.permission_gate,
        executor=container.tool_executor,
        audit_recorder=container.audit_recorder,
        tool_registry=container.tool_registry,
        request_registry=container.request_registry,
        sandbox_flow=sandbox_flow,
        approval_flow=approval_flow,
        fast_flow=fast_flow,
    )
