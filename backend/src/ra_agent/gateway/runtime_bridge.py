"""Internal Core envelope adapter for Runtime's durable execution services.

Gateway authorization remains the only entrance. Protected writes then run through
the existing SandboxFlow, including staging, safety checks, commit and recovery.
The public Core result contains the same fields as the standalone adapters.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PureWindowsPath
from typing import Any

from ra_agent.contracts import EffectStatus, ExecutionStatus, SourceType, ToolCallRequest
from ra_agent.contracts.core_v1 import ToolCallEnvelope
from ra_agent.core.config import Settings
from ra_agent.core.container import ServiceContainer
from ra_agent.execution.checkpoint import CheckpointStatus, FilesystemCheckpointManager
from ra_agent.execution.cleanup import RequestCleanupCoordinator
from ra_agent.execution.commit_gate import FilesystemCommitGate
from ra_agent.execution.download_manager import DownloadLifecycleManager
from ra_agent.execution.effect_manager import EffectManager
from ra_agent.execution.effect_store import FilesystemEffectStore
from ra_agent.execution.execution_provider import (
    ExecutionProviderServices,
    ExecutionServiceProvider,
)
from ra_agent.execution.executor import RegistryToolExecutor
from ra_agent.execution.pending_store import PendingStore
from ra_agent.execution.quarantine import FilesystemQuarantineStore
from ra_agent.execution.rollback import FilesystemRollbackManager
from ra_agent.execution.selective_rollback import SelectiveRollbackExecutor
from ra_agent.memory import FilesystemMemoryStore, MemoryLifecycleManager, MemoryNotFoundError
from ra_agent.runtime.sandbox_flow import SandboxFlow
from ra_agent.security.deep_checker import MockDeepSafetyChecker, RuleBasedDeepSafetyChecker
from ra_agent.security.pre_post_check import (
    MockPostExecutionChecker,
    MockPreExecutionChecker,
    RuleBasedPostExecutionChecker,
    RuleBasedPreExecutionChecker,
)
from ra_agent.security.risk_classifier import MockRiskClassifier, RuleBasedRiskClassifier
from ra_agent.security.rule_engine import RuleEngine
from ra_agent.tools import DEFAULT_TOOL_SPECS, ToolRegistry
from ra_agent.tools.download_guard import DownloadNetworkGuard
from ra_agent.tools.implementations.create_file import CreateFileHandler
from ra_agent.tools.implementations.delete_file import DeleteFileHandler
from ra_agent.tools.implementations.download_url import DownloadUrlHandler
from ra_agent.tools.implementations.egress import SendEmailDryRunHandler
from ra_agent.tools.implementations.list_dir import ListDirHandler
from ra_agent.tools.implementations.memory_tools import MemoryReadHandler, MemoryWriteHandler
from ra_agent.tools.implementations.read_file import ReadFileHandler
from ra_agent.tools.implementations.run_shell import RestrictedShellHandler
from ra_agent.tools.implementations.write_file import WriteFileHandler
from ra_agent.tools.path_resolver import PathResolutionError

from .adapters import (
    AdapterBypassError,
    CoreToolExecutor,
    ToolExecutionRejected,
    build_core_tool_executor,
)

_FILE_WRITES = frozenset({"create_file", "write_file", "delete_file"})
_PROTECTED_WRITES = _FILE_WRITES | {"memory_write"}
_SAFE_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,116}$")


def runtime_request_id(request_id: str) -> str:
    """Keep ordinary correlation IDs and encode IDs unsafe for durable store paths."""
    if (
        _SAFE_IDENTIFIER.fullmatch(request_id)
        and not request_id.endswith(".")
        and not request_id.startswith(("core-encoded-", "core-import-"))
        and not PureWindowsPath(request_id).is_reserved()
    ):
        return request_id
    return "core-encoded-" + sha256(request_id.encode("utf-8")).hexdigest()


class CoreRuntimeBridge:
    def __init__(
        self,
        *,
        services: ServiceContainer,
        adapter: CoreToolExecutor,
        rules: RuleEngine,
        pending_root: Path,
        quarantine_root: Path,
    ) -> None:
        self.services = services
        self.adapter = adapter
        self._gateway_token: object | None = None
        self._memory_lock = asyncio.Lock()
        specs = {spec.name: spec for spec in services.tool_registry.list_specs()}
        # Offline is a planner mode, never permission to replace Core file I/O with
        # mock effects or to skip payload validation. Leave legacy policy slots intact.
        self._classifier = (
            RuleBasedRiskClassifier(rules, specs)
            if isinstance(services.risk_classifier, MockRiskClassifier)
            else services.risk_classifier
        )
        self.sandbox_flow = SandboxFlow(
            checkpoint_manager=services.checkpoint_manager,
            executor=services.tool_executor,
            deep_checker=(
                RuleBasedDeepSafetyChecker(
                    rules, adapter.path_resolver.workspace_root, pending_root
                )
                if isinstance(services.deep_safety_checker, MockDeepSafetyChecker)
                else services.deep_safety_checker
            ),
            pre_checker=(
                RuleBasedPreExecutionChecker(rules, specs)
                if isinstance(services.pre_execution_checker, MockPreExecutionChecker)
                else services.pre_execution_checker
            ),
            post_checker=(
                RuleBasedPostExecutionChecker(
                    rules, adapter.path_resolver.workspace_root, pending_root, quarantine_root
                )
                if isinstance(services.post_execution_checker, MockPostExecutionChecker)
                else services.post_execution_checker
            ),
            commit_gate=services.commit_gate,
            rollback_manager=services.rollback_manager,
            audit_recorder=services.audit_recorder,
            cleanup_coordinator=services.cleanup_coordinator,
            execution_monitor=services.execution_monitor,
        )

    def bind_gateway(self, token: object) -> None:
        if self._gateway_token is not None and self._gateway_token is not token:
            raise RuntimeError("CoreRuntimeBridge is already bound to another ToolGateway")
        self.adapter.bind_gateway(token)
        self._gateway_token = token

    async def execute(
        self, envelope: ToolCallEnvelope, *, gateway_token: object | None = None
    ) -> dict[str, Any]:
        if self._gateway_token is None or gateway_token is not self._gateway_token:
            raise AdapterBypassError("Runtime bridge may only be invoked by ToolGateway")
        if envelope.tool not in _PROTECTED_WRITES | {"memory_read"}:
            # These adapters retain bounded reads, restricted process execution and
            # the explicit local-only simulated outbox; no network sender is used.
            return await self.adapter.execute(envelope, gateway_token=gateway_token)
        request = self._request(envelope)
        if envelope.tool.startswith("memory_"):
            async with self._memory_lock:
                await self._import_legacy_memory_key(str(request.arguments["key"]))
                if envelope.tool == "memory_read":
                    manager = self.services.memory_manager
                    assert manager is not None
                    trusted = await manager.store.get_trusted_value(envelope.resource)
                    return {
                        "tool": envelope.tool,
                        "key": envelope.resource,
                        "found": trusted is not None,
                        "value": trusted[1] if trusted is not None else None,
                    }
                receipt = await self._execute_protected(request)
                return {
                    "tool": envelope.tool,
                    "key": envelope.resource,
                    "stored": True,
                    "runtime": receipt,
                }
        receipt = await self._execute_protected(request)
        if envelope.tool == "delete_file":
            return {
                "tool": envelope.tool,
                "path": envelope.resource,
                "deleted": True,
                "runtime": receipt,
            }
        return {
            "tool": envelope.tool,
            "path": envelope.resource,
            "bytes_written": len(request.arguments["content"].encode("utf-8")),
            "runtime": receipt,
        }

    def _request(self, envelope: ToolCallEnvelope) -> ToolCallRequest:
        arguments = dict(envelope.canonical_args)
        field = "key" if envelope.tool.startswith("memory_") else "path"
        resource = arguments.setdefault(field, envelope.resource)
        if not isinstance(resource, str) or resource != envelope.resource or not resource:
            raise ToolExecutionRejected(f"{field} must exactly match ToolCallEnvelope.resource")
        if envelope.tool in _FILE_WRITES:
            try:
                if envelope.tool == "delete_file":
                    self.adapter.path_resolver.resolve_delete_target(resource)
                else:
                    content = arguments.get("content")
                    if not isinstance(content, str):
                        raise ToolExecutionRejected(f"{envelope.tool} requires string content")
                    self.adapter.path_resolver.validate_write_content(content)
                    target = self.adapter.path_resolver.resolve_write_target(resource)
                    if envelope.tool == "create_file" and target.exists():
                        raise ToolExecutionRejected(
                            f"create_file target already exists: {resource}"
                        )
            except PathResolutionError as error:
                raise ToolExecutionRejected(str(error)) from error
        if envelope.tool == "memory_write":
            arguments.setdefault("value", None)
        return ToolCallRequest(
            task_id=envelope.task_id,
            step_id="core-" + sha256(envelope.request_id.encode("utf-8")).hexdigest(),
            request_id=runtime_request_id(envelope.request_id),
            tool_name=envelope.tool,
            arguments=arguments,
            objective="Execute the Core tool call authorized by ToolGateway",
            context_summary=f"Core contract {envelope.contract_ref.contract_id} "
            f"version {envelope.contract_ref.version}; request {envelope.request_id}",
            source_type=SourceType.AGENT,
            requested_at=datetime.now(UTC),
        )

    async def _execute_protected(self, request: ToolCallRequest) -> dict[str, Any]:
        verdict = await self._classifier.classify(request)
        result = await self.sandbox_flow.run(request, verdict)
        if result.status is not ExecutionStatus.COMMITTED:
            reason = result.error or f"Runtime execution {result.status.value}"
            if result.status in {ExecutionStatus.BLOCKED, ExecutionStatus.ROLLED_BACK}:
                raise ToolExecutionRejected(reason)
            # A failed recovery does not establish that the side effect was undone.
            # Keep this distinct from a safe rejection in Gateway's durable outcome.
            raise RuntimeError(reason)
        store = self.services.effect_store
        assert store is not None
        effect = await store.get_by_request_id(request.request_id)
        if (
            effect is None
            or effect.status is not EffectStatus.COMMITTED
            or effect.task_id != request.task_id
            or effect.step_id != request.step_id
            or effect.checkpoint_id != result.checkpoint_id
            or not await store.verify_integrity(request.request_id)
        ):
            raise RuntimeError("Runtime commit has no matching durable committed effect")
        if request.tool_name in _FILE_WRITES:
            checkpoints = self.services.checkpoint_manager
            if (
                not isinstance(checkpoints, FilesystemCheckpointManager)
                or effect.checkpoint_id is None
            ):
                raise RuntimeError("Runtime file commit has no durable checkpoint")
            checkpoint = await checkpoints.get(effect.checkpoint_id)
            if (
                checkpoint.status is not CheckpointStatus.COMMITTED
                or checkpoint.request_id != request.request_id
                or checkpoint.task_id != request.task_id
                or checkpoint.step_id != request.step_id
            ):
                raise RuntimeError("Runtime commit checkpoint correlation does not match")
        return {
            "request_id": request.request_id,
            "checkpoint_id": effect.checkpoint_id,
            "effect_id": effect.effect_id,
            "commit_status": effect.status.value,
        }

    async def _import_legacy_memory_key(self, key: str) -> None:
        """Import an existing Core JSON value once through normal Runtime checks.

        The old file becomes a migration source. Later reads use only trusted
        versions, so rolling back a version cannot resurrect it from a JSON mirror.
        """
        manager = self.services.memory_manager
        assert manager is not None
        if (
            not self.adapter.memory_path.exists()
            or await manager.store.get_trusted(key) is not None
        ):
            return
        migration_id = (
            "core-import-" + sha256(f"{self.adapter.memory_path}\0{key}".encode()).hexdigest()
        )
        try:
            await manager.store.get(migration_id)
        except MemoryNotFoundError:
            pass
        else:
            return
        legacy = await asyncio.to_thread(self.adapter._load_memory)
        if key not in legacy:
            return
        await self._execute_protected(
            ToolCallRequest(
                task_id="core-legacy-memory",
                step_id=migration_id,
                request_id=migration_id,
                tool_name="memory_write",
                arguments={"key": key, "value": legacy[key]},
                objective="Migrate existing Core memory into Runtime trusted storage",
                context_summary="Import the deployment-owned legacy memory store",
                source_type=SourceType.USER,
                requested_at=datetime.now(UTC),
            )
        )


def build_core_runtime_bridge(settings: Settings, services: ServiceContainer) -> CoreRuntimeBridge:
    """Return the bridge and its shared execution container, including offline use.

    Callers install ``bridge.services`` as their application service container
    before constructing either Runtime scheduler. Existing live services are reused.
    """
    adapter = build_core_tool_executor(settings)
    rules = RuleEngine.from_directory(settings.security_config_dir)
    pending_root = settings.pending_root
    checkpoint_root = settings.checkpoint_root
    quarantine_root = settings.quarantine_root
    if not isinstance(services.tool_executor, RegistryToolExecutor):
        # Existing Core callers commonly customize their data/workspace roots alone.
        # Respect every explicit Runtime path and keep omitted defaults with Core data.
        data_root = settings.core_event_log_path.parent / "runtime"
        if "pending_root" not in settings.model_fields_set:
            pending_root = data_root / "pending"
        if "checkpoint_root" not in settings.model_fields_set:
            checkpoint_root = data_root / "checkpoints"
        if "quarantine_root" not in settings.model_fields_set:
            quarantine_root = data_root / "quarantine"
        pending = PendingStore(pending_root)
        checkpoints = FilesystemCheckpointManager(
            checkpoint_root,
            adapter.path_resolver,
            max_backup_bytes=settings.max_backup_bytes or settings.max_read_bytes,
        )
        rollback = FilesystemRollbackManager(adapter.path_resolver, pending, checkpoints)
        effects = FilesystemEffectStore(pending_root.parent / "effects")
        effect_manager = EffectManager(effects)
        memory = FilesystemMemoryStore(
            pending_root / "memory", max_value_bytes=rules.max_memory_characters
        )
        memory_manager = MemoryLifecycleManager(memory, effect_manager)
        quarantine = FilesystemQuarantineStore(
            quarantine_root, max_download_bytes=rules.max_download_bytes
        )
        downloads = DownloadLifecycleManager(quarantine, effect_manager)
        cleanup = RequestCleanupCoordinator(
            pending_store=pending,
            rollback_manager=rollback,
            memory_manager=memory_manager,
            download_manager=downloads,
            effect_manager=effect_manager,
        )
        handlers = {
            "create_file": CreateFileHandler(adapter.path_resolver, pending),
            "write_file": WriteFileHandler(adapter.path_resolver, pending),
            "delete_file": DeleteFileHandler(adapter.path_resolver, pending),
            "read_file": ReadFileHandler(adapter.path_resolver),
            "list_dir": ListDirHandler(
                adapter.path_resolver, max_entries=settings.max_list_entries
            ),
            "memory_write": MemoryWriteHandler(memory),
            "memory_read": MemoryReadHandler(memory),
            "download_url": DownloadUrlHandler(quarantine, DownloadNetworkGuard()),
            "send_email_dry_run": SendEmailDryRunHandler(),
        }
        if adapter.process_policy is not None and adapter.process_runner is not None:
            handlers["run_shell"] = RestrictedShellHandler(
                adapter.process_policy, adapter.process_runner
            )
        registry = ToolRegistry()
        for spec in DEFAULT_TOOL_SPECS:
            registry.register(spec.model_copy(deep=True), handlers.get(spec.name))
        executor = RegistryToolExecutor(
            registry,
            pending,
            memory_store=memory,
            quarantine_store=quarantine,
            cleanup_coordinator=cleanup,
            effect_manager=effect_manager,
        )
        services = ExecutionServiceProvider(
            ExecutionProviderServices(
                tool_executor=executor,
                checkpoint_manager=checkpoints,
                commit_gate=FilesystemCommitGate(
                    adapter.path_resolver, pending, checkpoints, effect_manager
                ),
                rollback_manager=rollback,
                cleanup_coordinator=cleanup,
                effect_store=effects,
                effect_manager=effect_manager,
                selective_rollback=SelectiveRollbackExecutor(
                    effect_store=effects,
                    effect_manager=effect_manager,
                    checkpoint_manager=checkpoints,
                    rollback_manager=rollback,
                    memory_manager=memory_manager,
                    download_manager=downloads,
                ),
                memory_manager=memory_manager,
                download_manager=downloads,
            )
        ).install(replace(services, tool_registry=registry))
    if any(
        service is None
        for service in (
            services.effect_store,
            services.effect_manager,
            services.memory_manager,
            services.cleanup_coordinator,
            services.selective_rollback_executor,
        )
    ):
        raise ValueError("Core requires complete Runtime effect, commit and recovery services")
    if services.memory_manager is not None:
        pending_root = services.memory_manager.store.memory_root.parent
    return CoreRuntimeBridge(
        services=services,
        adapter=adapter,
        rules=rules,
        pending_root=pending_root,
        quarantine_root=quarantine_root,
    )
