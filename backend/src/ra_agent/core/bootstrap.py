from ra_agent.audit import InMemoryAuditRecorder
from ra_agent.execution import MockToolExecutor
from ra_agent.runtime import InMemoryRequestExecutionRegistry, RuntimeScheduler
from ra_agent.security import (
    MockDeepSafetyChecker,
    MockPermissionGate,
    MockPolicyEngine,
    MockRiskClassifier,
)
from ra_agent.tools import DEFAULT_TOOL_SPECS, ToolRegistry

from .container import ServiceContainer


def build_mock_container() -> ServiceContainer:
    registry = ToolRegistry()
    for spec in DEFAULT_TOOL_SPECS:
        registry.register(spec.model_copy(deep=True))

    return ServiceContainer(
        risk_classifier=MockRiskClassifier(),
        policy_engine=MockPolicyEngine(),
        permission_gate=MockPermissionGate(),
        tool_executor=MockToolExecutor(),
        deep_safety_checker=MockDeepSafetyChecker(),
        audit_recorder=InMemoryAuditRecorder(),
        tool_registry=registry,
        request_registry=InMemoryRequestExecutionRegistry(),
    )


def build_runtime_scheduler(container: ServiceContainer) -> RuntimeScheduler:
    return RuntimeScheduler(
        classifier=container.risk_classifier,
        policy=container.policy_engine,
        permission_gate=container.permission_gate,
        executor=container.tool_executor,
        audit_recorder=container.audit_recorder,
        tool_registry=container.tool_registry,
        request_registry=container.request_registry,
    )
