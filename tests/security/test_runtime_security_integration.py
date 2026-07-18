import hashlib
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ra_agent.contracts import (
    ApprovalDecision,
    ExecutionStatus,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.core.bootstrap import build_mock_container, build_runtime_scheduler
from ra_agent.security.deep_checker import RuleBasedDeepSafetyChecker
from ra_agent.security.permission_gate import RuleBasedPermissionGate
from ra_agent.security.policy_engine import RuleBasedPolicyEngine
from ra_agent.security.risk_classifier import RuleBasedRiskClassifier
from ra_agent.security.rule_engine import RuleEngine


class PendingFixtureExecutor:
    def __init__(self, pending_root: Path) -> None:
        self.pending_root = pending_root

    async def execute(
        self,
        request: ToolCallRequest,
        *,
        checkpoint_id: str | None = None,
        approval_decision: ApprovalDecision | None = None,
    ) -> ToolExecutionResult:
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
        pending_path = f"{request.request_id}/payload.bin"
        payload_file = self.pending_root / pending_path
        payload_file.parent.mkdir(parents=True, exist_ok=True)
        payload_file.write_bytes(payload)
        changes = [
            {
                "version": 1,
                "request_id": request.request_id,
                "checkpoint_id": checkpoint_id,
                "tool_name": "write_file",
                "operation": "WRITE",
                "target_path": path,
                "pending_path": pending_path,
                "content_sha256": hashlib.sha256(payload).hexdigest(),
                "size_bytes": len(payload),
                "created_at": datetime.now(UTC).isoformat(),
                "status": "PENDING",
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
    container = replace(
        container,
        risk_classifier=RuleBasedRiskClassifier(rules, tool_specs),
        policy_engine=RuleBasedPolicyEngine(rules),
        permission_gate=RuleBasedPermissionGate(rules),
        deep_safety_checker=RuleBasedDeepSafetyChecker(
            rules, workspace_root, pending_root
        ),
        tool_executor=PendingFixtureExecutor(pending_root),
    )
    scheduler = build_runtime_scheduler(container)

    low = await scheduler.schedule(
        request_factory(
            "list_dir", arguments={"path": "docs"}, request_id="runtime-low"
        )
    )
    medium = await scheduler.schedule(
        request_factory(
            "write_file",
            arguments={"path": "draft.txt", "content": "safe"},
            request_id="runtime-medium",
        )
    )
    sensitive = await scheduler.schedule(
        request_factory(
            "read_file", arguments={"path": ".env"}, request_id="runtime-sensitive"
        )
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
