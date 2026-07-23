"""Live-container E2E checks with no LLM or public-network dependency."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from ra_agent.agent import AgentRuntime, MockPlanner
from ra_agent.agent.state import AgentRunStatus
from ra_agent.contracts import SourceType, TaskContract, ToolCallRequest
from ra_agent.core.bootstrap import build_runtime_container, build_runtime_scheduler
from ra_agent.core.config import RuntimeMode, Settings
from ra_agent.database.migrate import upgrade_database


def _settings(tmp_path: Path) -> Settings:
    runtime_root = tmp_path / ".runtime"
    return Settings.model_validate(
        {
            "runtime_mode": RuntimeMode.LIVE_AGENT,
            "database_url": f"sqlite+aiosqlite:///{(tmp_path / 'runtime.db').as_posix()}",
            "security_config_dir": Path(__file__).resolve().parents[2] / "configs",
            "workspace_root": runtime_root / "workspace",
            "pending_root": runtime_root / "pending",
            "checkpoint_root": runtime_root / "checkpoints",
            "quarantine_root": runtime_root / "quarantine",
        }
    )


def _request(
    task_id: str,
    tool_name: str,
    arguments: dict[str, object],
    objective: str,
    *,
    allow_egress: bool = False,
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id=task_id,
        step_id=f"step-{tool_name}",
        request_id=f"request-{tool_name}",
        tool_name=tool_name,
        arguments=arguments,
        objective=objective,
        context_summary="pytest live runtime E2E",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
        task_contract=TaskContract(
            allowed_actions=[tool_name],
            allowed_resources=["*"],
            max_affected_objects=1,
            allow_egress=allow_egress,
        ),
    )


def test_agent_commits_controlled_write_through_live_runtime(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    upgrade_database(settings.database_url)
    task_id = "task-e2e-write"
    runner = AgentRuntime(
        planner=MockPlanner(
            [
                _request(
                    task_id,
                    "write_file",
                    {"path": "notes.txt", "content": "safe E2E write"},
                    "Create a single safe notes file.",
                )
            ]
        ),
        scheduler=build_runtime_scheduler(build_runtime_container(settings)),
    )

    state = asyncio.run(runner.run(task_id, "Create a single safe notes file."))

    assert state.status is AgentRunStatus.COMPLETED
    assert (settings.workspace_root / "notes.txt").read_text(encoding="utf-8") == "safe E2E write"
    assert not list(settings.pending_root.rglob("*.json"))


def test_agent_blocks_dangerous_shell_before_any_process_runs(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    upgrade_database(settings.database_url)
    settings.workspace_root.mkdir(parents=True)
    readme = settings.workspace_root / "README.md"
    readme.write_text("safe fixture", encoding="utf-8")
    task_id = "task-e2e-shell"
    runner = AgentRuntime(
        planner=MockPlanner(
            [
                _request(
                    task_id,
                    "run_shell",
                    {"command": "rm -rf ./*"},
                    "Delete every file in the workspace.",
                )
            ]
        ),
        scheduler=build_runtime_scheduler(build_runtime_container(settings)),
    )

    state = asyncio.run(runner.run(task_id, "Delete every file in the workspace."))

    assert state.status is AgentRunStatus.BLOCKED
    assert readme.read_text(encoding="utf-8") == "safe fixture"


def test_agent_records_pending_egress_without_network_send(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    upgrade_database(settings.database_url)
    task_id = "task-e2e-egress"
    runner = AgentRuntime(
        planner=MockPlanner(
            [
                _request(
                    task_id,
                    "send_email_dry_run",
                    {
                        "recipient": "finance@example.com",
                        "artifact": {
                            "artifact_id": "report-1",
                            "owner": "finance",
                            "sensitivity": "SECRET",
                            "source": "workspace/report.csv",
                            "allowed_recipients": ["finance@example.com"],
                        },
                    },
                    "Prepare a finance review email without sending it.",
                    allow_egress=True,
                )
            ]
        ),
        scheduler=build_runtime_scheduler(build_runtime_container(settings)),
    )

    state = asyncio.run(runner.run(task_id, "Prepare a finance review email."))

    assert state.status is AgentRunStatus.COMPLETED
    assert state.results[0].output["status"] == "PENDING_EGRESS"
