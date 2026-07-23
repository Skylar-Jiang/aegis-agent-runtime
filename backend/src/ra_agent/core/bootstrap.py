import sys
from dataclasses import replace
from pathlib import Path

from sqlalchemy.engine import make_url

from ra_agent.audit import InMemoryAuditRecorder
from ra_agent.contracts import ToolSpec
from ra_agent.execution import (
    MockCheckpointManager,
    MockCommitGate,
    MockRollbackManager,
    MockToolExecutor,
)
from ra_agent.execution.checkpoint import FilesystemCheckpointManager
from ra_agent.execution.cleanup import RequestCleanupCoordinator
from ra_agent.execution.commit_gate import FilesystemCommitGate
from ra_agent.execution.download_manager import DownloadLifecycleManager
from ra_agent.execution.executor import RegistryToolExecutor
from ra_agent.execution.pending_store import PendingStore
from ra_agent.execution.process_runner import RestrictedProcessRunner
from ra_agent.execution.quarantine import FilesystemQuarantineStore
from ra_agent.execution.rollback import FilesystemRollbackManager
from ra_agent.memory import FilesystemMemoryStore, MemoryLifecycleManager
from ra_agent.runtime import InMemoryRequestExecutionRegistry, RuntimeScheduler
from ra_agent.runtime.approval_flow import ApprovalFlow
from ra_agent.runtime.fast_flow import FastExecutionFlow
from ra_agent.runtime.sandbox_flow import SandboxFlow
from ra_agent.security import (
    MockApprovalService,
    MockDeepSafetyChecker,
    MockPermissionGate,
    MockPolicyEngine,
    MockPostExecutionChecker,
    MockPreExecutionChecker,
    MockRiskClassifier,
    RuleBasedIntentBoundaryGuard,
)
from ra_agent.security.deep_checker import RuleBasedDeepSafetyChecker
from ra_agent.security.permission_gate import RuleBasedPermissionGate
from ra_agent.security.policy_engine import RuleBasedPolicyEngine
from ra_agent.security.pre_post_check import (
    RuleBasedPostExecutionChecker,
    RuleBasedPreExecutionChecker,
)
from ra_agent.security.risk_classifier import RuleBasedRiskClassifier
from ra_agent.security.rule_engine import RuleEngine
from ra_agent.tools import DEFAULT_TOOL_SPECS, MockToolHandler, ToolRegistry
from ra_agent.tools.download_guard import DownloadNetworkGuard
from ra_agent.tools.implementations.delete_file import DeleteFileHandler
from ra_agent.tools.implementations.download_url import DownloadUrlHandler
from ra_agent.tools.implementations.egress import SendEmailDryRunHandler
from ra_agent.tools.implementations.list_dir import ListDirHandler
from ra_agent.tools.implementations.memory_tools import MemoryReadHandler, MemoryWriteHandler
from ra_agent.tools.implementations.read_file import ReadFileHandler
from ra_agent.tools.implementations.run_shell import RestrictedShellHandler
from ra_agent.tools.implementations.write_file import WriteFileHandler
from ra_agent.tools.path_resolver import SafePathResolver
from ra_agent.tools.shell_policy import RestrictedShellPolicy

from .config import RuntimeMode, Settings
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
        pre_execution_checker=MockPreExecutionChecker(),
        post_execution_checker=MockPostExecutionChecker(),
        checkpoint_manager=MockCheckpointManager(),
        commit_gate=MockCommitGate(),
        rollback_manager=MockRollbackManager(),
        approval_service=MockApprovalService(),
        audit_recorder=InMemoryAuditRecorder(),
        tool_registry=registry,
        request_registry=InMemoryRequestExecutionRegistry(),
    )


def build_runtime_container(settings: Settings) -> ServiceContainer:
    """Compose the explicitly selected Runtime mode without changing Scheduler flows."""

    if settings.runtime_mode is RuntimeMode.OFFLINE:
        return build_mock_container()

    from ra_agent.database.container import build_persistent_container

    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    _prepare_database_parent(settings.database_url)
    rules = RuleEngine.from_directory(settings.security_config_dir, strict=True)
    container = build_persistent_container(settings.database_url)
    container = replace(
        container,
        risk_classifier=RuleBasedRiskClassifier(rules, _tool_specs()),
        policy_engine=RuleBasedPolicyEngine(rules),
        permission_gate=RuleBasedPermissionGate(rules),
        deep_safety_checker=RuleBasedDeepSafetyChecker(
            rules,
            settings.workspace_root,
            settings.pending_root,
        ),
        pre_execution_checker=RuleBasedPreExecutionChecker(rules, _tool_specs()),
        post_execution_checker=RuleBasedPostExecutionChecker(
            rules,
            settings.workspace_root,
            settings.pending_root,
            settings.quarantine_root,
        ),
        intent_boundary_guard=RuleBasedIntentBoundaryGuard(),
    )
    if settings.runtime_mode is RuntimeMode.RULES_ONLY:
        return container

    resolver = SafePathResolver(
        settings.workspace_root,
        sensitive_patterns=rules.sensitive_patterns,
        max_path_length=settings.max_path_length,
        max_read_bytes=settings.max_read_bytes,
        max_write_bytes=settings.max_write_bytes,
    )
    pending_store = PendingStore(settings.pending_root)
    checkpoint_manager = FilesystemCheckpointManager(settings.checkpoint_root, resolver)
    rollback_manager = FilesystemRollbackManager(resolver, pending_store, checkpoint_manager)
    quarantine_store = FilesystemQuarantineStore(
        settings.quarantine_root,
        max_download_bytes=rules.max_download_bytes,
    )
    memory_store = FilesystemMemoryStore(
        settings.pending_root / "memory",
        max_value_bytes=rules.max_memory_characters,
    )
    cleanup_coordinator = RequestCleanupCoordinator(
        pending_store=pending_store,
        rollback_manager=rollback_manager,
        memory_manager=MemoryLifecycleManager(memory_store),
        download_manager=DownloadLifecycleManager(quarantine_store),
    )
    registry = _build_live_registry(
        resolver,
        pending_store,
        settings.max_list_entries,
        quarantine_store,
        memory_store,
        settings.security_config_dir / "tool_policies.yaml",
    )
    return replace(
        container,
        tool_registry=registry,
        tool_executor=RegistryToolExecutor(
            registry,
            pending_store,
            memory_store=memory_store,
            quarantine_store=quarantine_store,
            cleanup_coordinator=cleanup_coordinator,
        ),
        checkpoint_manager=checkpoint_manager,
        commit_gate=FilesystemCommitGate(resolver, pending_store, checkpoint_manager),
        rollback_manager=rollback_manager,
        cleanup_coordinator=cleanup_coordinator,
    )


def build_agent_runner(settings: Settings, container: ServiceContainer):
    """Build an AgentRuntime that can only submit work to RuntimeScheduler."""

    from ra_agent.agent import DeepSeekClient, DeepSeekPlanner, UnavailablePlanner, build_graph

    scheduler = build_runtime_scheduler(container)
    if (
        settings.runtime_mode is RuntimeMode.LIVE_AGENT
        and settings.llm_base_url
        and settings.llm_api_key
        and settings.planner_model
    ):
        planner = DeepSeekPlanner(
            DeepSeekClient(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                model=settings.planner_model,
            ),
            allowed_tools=set(container.tool_registry.names()),
        )
    else:
        planner = UnavailablePlanner("live DeepSeek planning is not configured")
    return build_graph(planner=planner, scheduler=scheduler, max_turns=settings.max_agent_turns)


def _tool_specs() -> dict[str, ToolSpec]:
    return {spec.name: spec.model_copy(deep=True) for spec in DEFAULT_TOOL_SPECS}


def _prepare_database_parent(database_url: str) -> None:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        raise ValueError("Runtime persistence supports SQLite URLs only")
    if url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)


def _build_live_registry(
    resolver: SafePathResolver,
    pending_store: PendingStore,
    max_list_entries: int,
    quarantine_store: FilesystemQuarantineStore,
    memory_store: FilesystemMemoryStore,
    tool_policies_path: Path,
) -> ToolRegistry:
    executable_root = Path(sys.executable).resolve().parent.parent
    shell_policy = RestrictedShellPolicy.from_yaml(
        tool_policies_path,
        resolver,
        trusted_executable_roots=(executable_root,),
    )
    handlers = {
        "list_dir": ListDirHandler(resolver, max_entries=max_list_entries),
        "read_file": ReadFileHandler(resolver),
        "write_file": WriteFileHandler(resolver, pending_store),
        "delete_file": DeleteFileHandler(resolver, pending_store),
        "download_url": DownloadUrlHandler(quarantine_store, DownloadNetworkGuard()),
        "memory_read": MemoryReadHandler(memory_store),
        "memory_write": MemoryWriteHandler(memory_store),
        "send_email_dry_run": SendEmailDryRunHandler(),
        "run_shell": RestrictedShellHandler(
            shell_policy,
            RestrictedProcessRunner.from_yaml(tool_policies_path),
        ),
    }
    registry = ToolRegistry()
    for spec in DEFAULT_TOOL_SPECS:
        registry.register(spec.model_copy(deep=True), handlers.get(spec.name))
    return registry


def build_runtime_scheduler(container: ServiceContainer) -> RuntimeScheduler:
    fast_flow = FastExecutionFlow(
        executor=container.tool_executor,
        pre_checker=container.pre_execution_checker,
        post_checker=container.post_execution_checker,
        audit_recorder=container.audit_recorder,
    )
    sandbox_flow = SandboxFlow(
        checkpoint_manager=container.checkpoint_manager,
        executor=container.tool_executor,
        deep_checker=container.deep_safety_checker,
        pre_checker=container.pre_execution_checker,
        post_checker=container.post_execution_checker,
        commit_gate=container.commit_gate,
        rollback_manager=container.rollback_manager,
        audit_recorder=container.audit_recorder,
        cleanup_coordinator=container.cleanup_coordinator,
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
        intent_boundary_guard=container.intent_boundary_guard,
    )
