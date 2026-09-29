from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from ra_agent.contracts import (
    ApprovalDecision,
    ExecutionStatus,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.core.bootstrap import build_mock_container, build_runtime_scheduler
from ra_agent.execution.checkpoint import FilesystemCheckpointManager
from ra_agent.execution.commit_gate import FilesystemCommitGate
from ra_agent.execution.executor import RegistryToolExecutor
from ra_agent.execution.pending_store import PendingStatus, PendingStore
from ra_agent.execution.rollback import FilesystemRollbackManager
from ra_agent.security.deep_checker import RuleBasedDeepSafetyChecker
from ra_agent.security.permission_gate import RuleBasedPermissionGate
from ra_agent.security.policy_engine import RuleBasedPolicyEngine
from ra_agent.security.risk_classifier import RuleBasedRiskClassifier
from ra_agent.security.rule_engine import RuleEngine
from ra_agent.tools import DEFAULT_TOOL_SPECS, ToolRegistry
from ra_agent.tools.implementations.list_dir import ListDirHandler
from ra_agent.tools.implementations.write_file import WriteFileHandler
from ra_agent.tools.path_resolver import SafePathResolver


class PendingFixtureExecutor:
    def __init__(self, pending_root: Path) -> None:
        self.pending_store = PendingStore(pending_root)
        self.calls: list[ToolCallRequest] = []

    async def execute(
        self,
        request: ToolCallRequest,
        *,
        checkpoint_id: str | None = None,
        approval_decision: ApprovalDecision | None = None,
    ) -> ToolExecutionResult:
        self.calls.append(request)
        if checkpoint_id is None:
            return ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=ExecutionStatus.SUCCESS,
                output={"fixture": True},
            )
        path = request.arguments.get("path")
        content = request.arguments.get("content")
        assert isinstance(path, str)
        assert isinstance(content, str)
        payload = content.encode("utf-8")
        record = await self.pending_store.stage_write(request.request_id, path, payload)
        record = await self.pending_store.bind_checkpoint(request.request_id, checkpoint_id)
        changes = [
            {
                "version": 1,
                "request_id": record.request_id,
                "checkpoint_id": record.checkpoint_id,
                "tool_name": record.tool_name,
                "operation": record.operation.value,
                "target_path": record.target_path,
                "pending_path": record.pending_path,
                "content_sha256": record.content_sha256,
                "size_bytes": record.size_bytes,
                "created_at": record.created_at.isoformat(),
                "status": record.status.value,
            }
        ]
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            checkpoint_id=checkpoint_id,
            status=ExecutionStatus.PENDING_COMMIT,
            output={"fixture": True},
            pending_changes=changes,
        )


@pytest.mark.asyncio
async def test_real_security_components_follow_runtime_routes(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    pending_root = tmp_path / "pending"
    container = build_mock_container()
    tool_specs = {spec.name: spec for spec in container.tool_registry.list_specs()}
    executor = PendingFixtureExecutor(pending_root)
    container = replace(
        container,
        risk_classifier=RuleBasedRiskClassifier(rules, tool_specs),
        policy_engine=RuleBasedPolicyEngine(rules),
        permission_gate=RuleBasedPermissionGate(rules),
        deep_safety_checker=RuleBasedDeepSafetyChecker(rules, workspace_root, pending_root),
        tool_executor=executor,
    )
    scheduler = build_runtime_scheduler(container)

    low = await scheduler.schedule(
        request_factory("list_dir", arguments={"path": "docs"}, request_id="runtime-low")
    )
    medium = await scheduler.schedule(
        request_factory(
            "write_file",
            arguments={"path": "draft.txt", "content": "safe"},
            request_id="runtime-medium",
        )
    )
    sensitive = await scheduler.schedule(
        request_factory("read_file", arguments={"path": ".env"}, request_id="runtime-sensitive")
    )
    dangerous = await scheduler.schedule(
        request_factory(
            "run_shell",
            arguments={"command": "rm -rf ./data"},
            request_id="runtime-dangerous",
        )
    )
    traversal = await scheduler.schedule(
        request_factory(
            "read_file",
            arguments={"path": "../secret.txt"},
            request_id="runtime-traversal",
        )
    )

    assert low.status is ExecutionStatus.COMMITTED
    assert medium.status is ExecutionStatus.COMMITTED
    assert sensitive.status is ExecutionStatus.WAITING_APPROVAL
    assert dangerous.status is ExecutionStatus.BLOCKED
    assert traversal.status is ExecutionStatus.BLOCKED
    assert [request.request_id for request in executor.calls] == [
        "runtime-low",
        "runtime-medium",
    ]


@pytest.mark.asyncio
async def test_scheduler_executes_real_list_and_pending_write_components(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    docs = workspace_root / "docs"
    docs.mkdir(parents=True)
    (docs / "report.txt").write_text("real listing", encoding="utf-8")
    resolver = SafePathResolver(
        workspace_root,
        max_path_length=4096,
        max_read_bytes=1024,
        max_write_bytes=1024,
    )
    pending_store = PendingStore(tmp_path / "pending")
    checkpoint_manager = FilesystemCheckpointManager(
        tmp_path / "checkpoints",
        resolver,
    )
    registry = ToolRegistry()
    specs = {spec.name: spec for spec in DEFAULT_TOOL_SPECS}
    registry.register(specs["list_dir"], ListDirHandler(resolver, max_entries=100))
    registry.register(specs["write_file"], WriteFileHandler(resolver, pending_store))
    executor = RegistryToolExecutor(registry, pending_store)
    container = replace(
        build_mock_container(),
        risk_classifier=RuleBasedRiskClassifier(rules, specs),
        policy_engine=RuleBasedPolicyEngine(rules),
        permission_gate=RuleBasedPermissionGate(rules),
        deep_safety_checker=RuleBasedDeepSafetyChecker(
            rules,
            workspace_root,
            pending_store.pending_root,
        ),
        tool_executor=executor,
        checkpoint_manager=checkpoint_manager,
        commit_gate=FilesystemCommitGate(resolver, pending_store, checkpoint_manager),
        rollback_manager=FilesystemRollbackManager(
            resolver,
            pending_store,
            checkpoint_manager,
        ),
        tool_registry=registry,
    )
    scheduler = build_runtime_scheduler(container)

    listed = await scheduler.schedule(
        request_factory(
            "list_dir",
            arguments={"path": "docs"},
            request_id="real-runtime-list",
        )
    )
    written = await scheduler.schedule(
        request_factory(
            "write_file",
            arguments={"path": "draft.txt", "content": "real write"},
            request_id="real-runtime-write",
        )
    )

    assert listed.status is ExecutionStatus.COMMITTED
    assert listed.output["entries"] == [
        {
            "name": "report.txt",
            "path": "docs/report.txt",
            "type": "file",
            "size_bytes": len(b"real listing"),
        }
    ]
    assert written.status is ExecutionStatus.COMMITTED
    assert (workspace_root / "draft.txt").read_text(encoding="utf-8") == "real write"
    assert (await pending_store.get("real-runtime-write")).status is PendingStatus.COMMITTED
