from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from ra_agent.audit import InMemoryAuditRecorder, PersistentAuditRecorder
from ra_agent.contracts import ExecutionStatus, SourceType, ToolCallRequest
from ra_agent.core.bootstrap import (
    build_agent_runner,
    build_runtime_container,
    build_runtime_scheduler,
)
from ra_agent.core.config import RuntimeMode, Settings
from ra_agent.database.migrate import upgrade_database
from ra_agent.execution import MockToolExecutor
from ra_agent.execution.checkpoint import FilesystemCheckpointManager
from ra_agent.execution.commit_gate import FilesystemCommitGate
from ra_agent.execution.executor import RegistryToolExecutor
from ra_agent.execution.rollback import FilesystemRollbackManager
from ra_agent.main import create_app
from ra_agent.runtime import InMemoryRequestExecutionRegistry
from ra_agent.security.deep_checker import RuleBasedDeepSafetyChecker
from ra_agent.security.permission_gate import RuleBasedPermissionGate
from ra_agent.security.policy_engine import RuleBasedPolicyEngine
from ra_agent.security.risk_classifier import RuleBasedRiskClassifier


def _settings(tmp_path: Path, mode: RuntimeMode) -> Settings:
    runtime_root = tmp_path / ".runtime"
    return Settings.model_validate(
        {
            "runtime_mode": mode,
            "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'runtime.db').as_posix()}",
            "security_config_dir": Path(__file__).resolve().parents[3] / "configs",
            "workspace_root": runtime_root / "workspace",
            "pending_root": runtime_root / "pending",
            "checkpoint_root": runtime_root / "checkpoints",
            "quarantine_root": runtime_root / "quarantine",
        }
    )


def test_offline_mode_keeps_the_fixture_container(tmp_path: Path) -> None:
    container = build_runtime_container(_settings(tmp_path, RuntimeMode.OFFLINE))

    assert isinstance(container.tool_executor, MockToolExecutor)
    assert isinstance(container.audit_recorder, InMemoryAuditRecorder)
    assert isinstance(container.request_registry, InMemoryRequestExecutionRegistry)
    assert not (tmp_path / ".runtime").exists()


def test_rules_only_mode_uses_rules_and_persistent_state_without_real_tools(tmp_path: Path) -> None:
    container = build_runtime_container(_settings(tmp_path, RuntimeMode.RULES_ONLY))

    assert isinstance(container.risk_classifier, RuleBasedRiskClassifier)
    assert isinstance(container.policy_engine, RuleBasedPolicyEngine)
    assert isinstance(container.permission_gate, RuleBasedPermissionGate)
    assert isinstance(container.deep_safety_checker, RuleBasedDeepSafetyChecker)
    assert isinstance(container.tool_executor, MockToolExecutor)
    assert isinstance(container.audit_recorder, PersistentAuditRecorder)


def test_non_offline_mode_creates_the_custom_sqlite_parent(tmp_path: Path) -> None:
    settings = _settings(tmp_path, RuntimeMode.RULES_ONLY).model_copy(
        update={
            "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'state/runtime.db').as_posix()}"
        }
    )

    build_runtime_container(settings)

    assert (tmp_path / "state").is_dir()


def test_live_agent_mode_uses_real_controlled_file_components(tmp_path: Path) -> None:
    container = build_runtime_container(_settings(tmp_path, RuntimeMode.LIVE_AGENT))

    assert isinstance(container.tool_executor, RegistryToolExecutor)
    assert isinstance(container.checkpoint_manager, FilesystemCheckpointManager)
    assert isinstance(container.commit_gate, FilesystemCommitGate)
    assert isinstance(container.rollback_manager, FilesystemRollbackManager)
    assert type(container.tool_registry.get_handler("read_file")).__name__ == "ReadFileHandler"
    assert type(container.tool_registry.get_handler("write_file")).__name__ == "WriteFileHandler"


def test_unconfigured_live_agent_runner_fails_before_tool_scheduling(tmp_path: Path) -> None:
    container = build_runtime_container(_settings(tmp_path, RuntimeMode.LIVE_AGENT))
    runner = build_agent_runner(_settings(tmp_path, RuntimeMode.LIVE_AGENT), container)

    assert type(runner.planner).__name__ == "UnavailablePlanner"


def test_live_agent_mode_commits_a_staged_workspace_write(tmp_path: Path) -> None:
    settings = _settings(tmp_path, RuntimeMode.LIVE_AGENT)
    upgrade_database(settings.database_url)
    scheduler = build_runtime_scheduler(build_runtime_container(settings))
    request = ToolCallRequest(
        task_id="task-live",
        step_id="step-live",
        request_id="request-live-write",
        tool_name="write_file",
        arguments={"path": "result.txt", "content": "controlled write"},
        objective="write a controlled test file",
        context_summary="container integration test",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
    )

    result = asyncio.run(scheduler.schedule(request))

    assert result.status is ExecutionStatus.COMMITTED
    assert (
        (settings.workspace_root / "result.txt").read_text(encoding="utf-8")
        == "controlled write"
    )


def test_non_offline_app_runs_migrations_on_startup(tmp_path: Path) -> None:
    settings = _settings(tmp_path, RuntimeMode.RULES_ONLY)
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["data"]["mode"] == RuntimeMode.RULES_ONLY.value
    with sqlite3.connect(tmp_path / "runtime.db") as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"alembic_version", "audit_events", "approval_requests", "execution_claims"} <= tables
