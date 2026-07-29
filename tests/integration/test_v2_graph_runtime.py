from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from ra_agent.contracts import (
    ExecutionStatus,
    SourceType,
    TaskContract,
    TaskGraph,
    TaskNode,
    ToolCallRequest,
)
from ra_agent.core.bootstrap import (
    build_runtime_container,
    build_runtime_scheduler,
    build_task_graph_scheduler,
)
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


def _node(
    *,
    task_id: str,
    graph_id: str,
    node_id: str,
    tool_name: str,
    arguments: dict[str, object],
    contract: TaskContract,
    dependencies: list[str] | None = None,
    effect_targets: list[str] | None = None,
) -> TaskNode:
    return TaskNode(
        task_id=task_id,
        graph_id=graph_id,
        node_id=node_id,
        request=ToolCallRequest(
            task_id=task_id,
            step_id=node_id,
            request_id=f"request-{node_id}",
            tool_name=tool_name,
            arguments=arguments,
            objective="exercise controlled graph scheduling",
            context_summary="v2 graph integration test",
            source_type=SourceType.USER,
            requested_at=datetime.now(UTC),
            task_contract=contract,
        ),
        dependencies=dependencies or [],
        parallel_safe=True,
        effect_targets=effect_targets or [],
    )


def test_waiting_approval_pauses_only_conflicting_work_and_resumes_descendant(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    upgrade_database(settings.database_url)
    settings.workspace_root.mkdir(parents=True)
    (settings.workspace_root / "delete.txt").write_text("remove", encoding="utf-8")
    (settings.workspace_root / "read.txt").write_text("safe", encoding="utf-8")
    contract = TaskContract(
        allowed_actions=["delete_file", "read_file"],
        allowed_resources=["delete.txt", "read.txt"],
        max_affected_objects=2,
    )
    container = build_runtime_container(settings)
    scheduler = build_task_graph_scheduler(
        container,
        runtime_scheduler=build_runtime_scheduler(container),
    )
    graph = TaskGraph(
        graph_id="graph-approval",
        task_id="task-approval",
        max_parallelism=2,
        nodes=[
            _node(
                task_id="task-approval",
                graph_id="graph-approval",
                node_id="delete",
                tool_name="delete_file",
                arguments={"path": "delete.txt"},
                contract=contract,
                effect_targets=["file:delete.txt"],
            ),
            _node(
                task_id="task-approval",
                graph_id="graph-approval",
                node_id="read-independent",
                tool_name="read_file",
                arguments={"path": "read.txt"},
                contract=contract,
            ),
            _node(
                task_id="task-approval",
                graph_id="graph-approval",
                node_id="read-after-delete",
                tool_name="read_file",
                arguments={"path": "read.txt"},
                contract=contract,
                dependencies=["delete"],
            ),
        ],
    )

    first = asyncio.run(scheduler.schedule_graph(graph))
    approval_id = first.node_results.get("delete")

    assert approval_id is None
    assert first.node_results["read-independent"].status is ExecutionStatus.COMMITTED
    assert first.blocked_nodes["delete"] == "WAITING_APPROVAL"
    assert first.blocked_nodes["read-after-delete"] == "dependency_waiting_approval"

    pending = asyncio.run(container.approval_service.list_for_task("task-approval"))
    asyncio.run(container.approval_service.grant(pending[0].approval_id, "reviewer", "approved"))
    resumed = asyncio.run(scheduler.resume_after_approval("graph-approval", pending[0].approval_id))

    assert resumed.node_results["delete"].status is ExecutionStatus.COMMITTED
    assert resumed.node_results["read-after-delete"].status is ExecutionStatus.COMMITTED
    assert not (settings.workspace_root / "delete.txt").exists()


def test_shared_effect_target_is_serialized_through_live_runtime(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    upgrade_database(settings.database_url)
    contract = TaskContract(
        allowed_actions=["write_file"],
        allowed_resources=["same.txt"],
        max_affected_objects=1,
    )
    container = build_runtime_container(settings)
    scheduler = build_task_graph_scheduler(
        container,
        runtime_scheduler=build_runtime_scheduler(container),
    )
    graph = TaskGraph(
        graph_id="graph-conflict",
        task_id="task-conflict",
        max_parallelism=2,
        nodes=[
            _node(
                task_id="task-conflict",
                graph_id="graph-conflict",
                node_id="first",
                tool_name="write_file",
                arguments={"path": "same.txt", "content": "first"},
                contract=contract,
                effect_targets=["file:same.txt"],
            ),
            _node(
                task_id="task-conflict",
                graph_id="graph-conflict",
                node_id="second",
                tool_name="write_file",
                arguments={"path": "same.txt", "content": "second"},
                contract=contract,
                effect_targets=["file:same.txt"],
            ),
        ],
    )

    result = asyncio.run(scheduler.schedule_graph(graph))

    assert result.node_results["first"].status is ExecutionStatus.COMMITTED
    assert result.node_results["second"].status is ExecutionStatus.COMMITTED
    assert (settings.workspace_root / "same.txt").read_text(encoding="utf-8") == "second"
