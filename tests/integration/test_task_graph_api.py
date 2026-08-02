from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from ra_agent.contracts import (
    ApprovalRequest,
    EffectRecord,
    EffectStatus,
    ExecutionStatus,
    SourceType,
    TaskContract,
    TaskGraph,
    TaskGraphResult,
    TaskNode,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.core.config import RuntimeMode, Settings
from ra_agent.main import create_app


def _graph() -> TaskGraph:
    request = ToolCallRequest(
        task_id="graph-task",
        step_id="read-node",
        request_id="read-request",
        tool_name="read_file",
        arguments={"path": "README.md"},
        objective="read a safe fixture",
        context_summary="graph api integration test",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
        task_contract=TaskContract(
            allowed_actions=["read_file"],
            allowed_resources=["README.md"],
            max_affected_objects=1,
        ),
    )
    return TaskGraph(
        graph_id="graph-api",
        task_id=request.task_id,
        max_parallelism=1,
        nodes=[
            TaskNode(
                task_id=request.task_id,
                graph_id="graph-api",
                node_id="read-node",
                request=request,
                parallel_safe=True,
            )
        ],
    )


def _live_settings(tmp_path: Path) -> Settings:
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
class RecordingGraphScheduler:
    def __init__(self) -> None:
        self.graphs: dict[str, TaskGraphResult] = {}
        self.scheduled = asyncio.Event()
        self.cancelled: list[str] = []
        self.resumed: list[tuple[str, str]] = []

    async def schedule_graph(self, graph: TaskGraph) -> TaskGraphResult:
        self.scheduled.set()
        result = TaskGraphResult(
            graph_id=graph.graph_id,
            task_id=graph.task_id,
            node_results={
                "read-node": ToolExecutionResult(
                    task_id=graph.task_id,
                    step_id="read-node",
                    request_id="read-request",
                    status=ExecutionStatus.COMMITTED,
                    output={"untrusted_raw_output": "must not reach API"},
                )
            },
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        self.graphs[graph.graph_id] = result
        return result

    async def snapshot(self, graph_id: str) -> TaskGraphResult:
        return self.graphs[graph_id]

    async def cancel_graph(self, graph_id: str) -> TaskGraphResult:
        self.cancelled.append(graph_id)
        return self.graphs[graph_id]

    async def resume_after_approval(self, graph_id: str, approval_id: str) -> TaskGraphResult:
        self.resumed.append((graph_id, approval_id))
        return self.graphs[graph_id]


def test_graph_routes_submit_snapshot_cancel_resume_and_keep_output_redacted() -> None:
    app = create_app(Settings.model_validate({"runtime_mode": "offline"}))
    scheduler = RecordingGraphScheduler()
    app.state.task_graph_scheduler = scheduler
    graph = _graph()

    with TestClient(app) as client:
        submitted = client.post("/api/task-graphs", json=graph.model_dump(mode="json"))

        assert submitted.status_code == 200
        assert submitted.json()["data"]["status"] == "RUNNING"
        for _ in range(20):
            if scheduler.scheduled.is_set():
                break
            time.sleep(0.01)
        assert scheduler.scheduled.is_set()

        snapshot = client.get("/api/tasks/graph-task/graph")
        assert snapshot.status_code == 200
        node = snapshot.json()["data"]["nodes"][0]
        assert node["status"] == "COMMITTED"
        assert node["dependencies"] == []
        assert "started_at" in node
        assert "finished_at" in node
        assert "output" not in node
        assert "untrusted_raw_output" not in str(snapshot.json())

        resumed = client.post("/api/task-graphs/graph-api/resume", json={"approval_id": "a-1"})
        cancelled = client.post("/api/task-graphs/graph-api/cancel")

    assert resumed.status_code == 200
    assert cancelled.status_code == 200
    assert scheduler.resumed == [("graph-api", "a-1")]
    assert scheduler.cancelled == ["graph-api"]


def test_graph_task_approvals_and_effects_are_discoverable_by_task() -> None:
    app = create_app(Settings.model_validate({"runtime_mode": "offline"}))
    scheduler = RecordingGraphScheduler()
    app.state.task_graph_scheduler = scheduler
    graph = _graph()
    now = datetime.now(UTC)

    with TestClient(app) as client:
        client.post("/api/task-graphs", json=graph.model_dump(mode="json"))
        for _ in range(20):
            if scheduler.scheduled.is_set():
                break
            time.sleep(0.01)
        asyncio.run(
            app.state.services.approval_service.create(
                ApprovalRequest(
                    approval_id="graph-approval",
                    task_id=graph.task_id,
                    step_id="delete-node",
                    request_id="delete-request",
                    tool_name="delete_file",
                    request_fingerprint="fingerprint",
                    reason="high risk graph node",
                    requested_at=now,
                    expires_at=now + timedelta(minutes=5),
                )
            )
        )

        task_approvals = client.get("/api/tasks/graph-task/approvals")
        pending_approvals = client.get("/api/approvals?status=PENDING&task_id=graph-task")
        effects = client.get("/api/tasks/graph-task/effects")

    assert task_approvals.status_code == 200
    assert pending_approvals.status_code == 200
    assert task_approvals.json()["data"][0]["approval_id"] == "graph-approval"
    assert pending_approvals.json()["data"][0]["approval_id"] == "graph-approval"
    assert effects.status_code == 200
    assert effects.json()["data"] == []


def test_global_approvals_include_task_identity_and_decision_response() -> None:
    app = create_app(Settings.model_validate({"runtime_mode": "offline"}))
    scheduler = RecordingGraphScheduler()
    app.state.task_graph_scheduler = scheduler
    graph = _graph()
    now = datetime.now(UTC)

    with TestClient(app) as client:
        client.post("/api/task-graphs", json=graph.model_dump(mode="json"))
        asyncio.run(
            app.state.services.approval_service.create(
                ApprovalRequest(
                    approval_id="global-approval",
                    task_id=graph.task_id,
                    step_id="delete-node",
                    request_id="delete-request",
                    tool_name="delete_file",
                    request_fingerprint="fingerprint",
                    reason="high risk graph node",
                    requested_at=now,
                    expires_at=now + timedelta(minutes=5),
                )
            )
        )
        listed = client.get("/api/approvals?status=PENDING")
        granted = client.post(
            "/api/approvals/global-approval/grant?decided_by=reviewer"
        )

    assert listed.status_code == 200
    assert listed.json()["data"] == [
        {
            "approval_id": "global-approval",
            "task_id": "graph-task",
            "status": "PENDING",
            "tool_name": "delete_file",
            "reason": "high risk graph node",
            "step_id": "delete-node",
            "request_id": "delete-request",
        }
    ]
    assert granted.status_code == 200
    assert granted.json()["data"]["task_id"] == "graph-task"
    assert scheduler.resumed == [("graph-api", "global-approval")]


def test_denied_graph_approval_also_notifies_the_graph_scheduler() -> None:
    app = create_app(Settings.model_validate({"runtime_mode": "offline"}))
    scheduler = RecordingGraphScheduler()
    app.state.task_graph_scheduler = scheduler
    graph = _graph()
    now = datetime.now(UTC)

    with TestClient(app) as client:
        client.post("/api/task-graphs", json=graph.model_dump(mode="json"))
        asyncio.run(
            app.state.services.approval_service.create(
                ApprovalRequest(
                    approval_id="denied-approval",
                    task_id=graph.task_id,
                    step_id="delete-node",
                    request_id="delete-request",
                    tool_name="delete_file",
                    request_fingerprint="fingerprint",
                    reason="high risk graph node",
                    requested_at=now,
                    expires_at=now + timedelta(minutes=5),
                )
            )
        )
        denied = client.post(
            "/api/approvals/denied-approval/deny?decided_by=reviewer"
        )

    assert denied.status_code == 200
    assert denied.json()["data"]["task_id"] == "graph-task"
    assert scheduler.resumed == [("graph-api", "denied-approval")]


def test_graph_effect_projection_excludes_untrusted_effect_content() -> None:
    class EffectStore:
        async def list_by_task_id(self, task_id: str) -> tuple[EffectRecord, ...]:
            return (
                EffectRecord(
                    effect_id="effect-1",
                    task_id=task_id,
                    step_id="write-node",
                    request_id="write-request",
                    kind="filesystem",
                    target_ref="file:demo.txt",
                    status=EffectStatus.PENDING,
                    checkpoint_id="checkpoint-1",
                    artifact_refs=["pending/write-request"],
                    created_at=datetime.now(UTC),
                ),
            )

    app = create_app(Settings.model_validate({"runtime_mode": "offline"}))
    app.state.services = replace(app.state.services, effect_store=EffectStore())
    scheduler = RecordingGraphScheduler()
    app.state.task_graph_scheduler = scheduler
    graph = _graph()

    with TestClient(app) as client:
        client.post("/api/task-graphs", json=graph.model_dump(mode="json"))
        for _ in range(20):
            if scheduler.scheduled.is_set():
                break
            time.sleep(0.01)
        response = client.get("/api/tasks/graph-task/effects")

    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "effect_id": "effect-1",
            "kind": "filesystem",
            "target_ref": "file:demo.txt",
            "status": "PENDING",
            "checkpoint_id": "checkpoint-1",
            "artifact_refs": ["pending/write-request"],
            "created_at": response.json()["data"][0]["created_at"],
        }
    ]


def test_graph_submit_exposes_running_snapshot_before_background_execution() -> None:
    class DelayedScheduler:
        def __init__(self) -> None:
            self.results: dict[str, TaskGraphResult] = {}
            self.started = threading.Event()
            self.release = threading.Event()

        async def prepare_graph(self, graph: TaskGraph) -> TaskGraphResult:
            result = TaskGraphResult(
                graph_id=graph.graph_id,
                task_id=graph.task_id,
                started_at=datetime.now(UTC),
            )
            self.results[graph.graph_id] = result
            return result

        async def schedule_graph(self, graph: TaskGraph) -> TaskGraphResult:
            self.started.set()
            await asyncio.to_thread(self.release.wait)
            return self.results[graph.graph_id]

        async def snapshot(self, graph_id: str) -> TaskGraphResult:
            return self.results[graph_id]

        async def cancel_graph(self, graph_id: str) -> TaskGraphResult:
            self.release.set()
            return self.results[graph_id]

        async def resume_after_approval(self, graph_id: str, approval_id: str) -> TaskGraphResult:
            return self.results[graph_id]

    app = create_app(Settings.model_validate({"runtime_mode": "offline"}))
    scheduler = DelayedScheduler()
    app.state.task_graph_scheduler = scheduler
    graph = _graph()

    with TestClient(app) as client:
        submitted = client.post("/api/task-graphs", json=graph.model_dump(mode="json"))
        assert submitted.status_code == 200
        assert scheduler.started.wait(timeout=1)
        snapshot = client.get("/api/tasks/graph-task/graph")
        scheduler.release.set()

    assert snapshot.status_code == 200
    assert snapshot.json()["data"]["status"] == "RUNNING"


def test_graph_api_executes_a_real_runtime_node_and_emits_graph_audit(tmp_path: Path) -> None:
    settings = _live_settings(tmp_path)
    settings.workspace_root.mkdir(parents=True)
    (settings.workspace_root / "README.md").write_text("safe graph fixture", encoding="utf-8")
    app = create_app(settings)
    graph = _graph()

    with TestClient(app) as client:
        submitted = client.post("/api/task-graphs", json=graph.model_dump(mode="json"))
        assert submitted.status_code == 200
        snapshot = None
        for _ in range(50):
            response = client.get("/api/tasks/graph-task/graph")
            if response.status_code == 200:
                snapshot = response.json()["data"]
                if snapshot["status"] == "COMPLETED":
                    break
            time.sleep(0.02)
        events = client.get("/api/tasks/graph-task/events").json()["data"]["events"]

    assert snapshot is not None
    assert snapshot["nodes"][0]["status"] == "COMMITTED"
    assert all("output" not in event["details"] for event in events)
    assert any(event["details"].get("graph_id") == "graph-api" for event in events)


def test_demo_selective_rollback_uses_real_effects_and_preserves_independent_effect(
    tmp_path: Path,
) -> None:
    settings = _live_settings(tmp_path)
    app = create_app(settings)
    app.state.enable_demo_fixtures = True

    with TestClient(app) as client:
        created = client.post("/api/demo/selective-rollback")

        assert created.status_code == 200
        task_id = created.json()["data"]["task_id"]
        graph_id = created.json()["data"]["graph_id"]
        for _ in range(50):
            effects = client.get(f"/api/tasks/{task_id}/effects").json()["data"]
            if {effect["status"] for effect in effects} == {"COMMITTED", "PENDING"}:
                break
            time.sleep(0.02)
        cancelled = client.post(f"/api/task-graphs/{graph_id}/cancel")
        resume = client.post(
            f"/api/task-graphs/{graph_id}/resume",
            json={"approval_id": created.json()["data"]["approval_id"]},
        )
        effects = client.get(f"/api/tasks/{task_id}/effects").json()["data"]
        events = client.get(f"/api/tasks/{task_id}/events").json()["data"]["events"]

    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "FAILED"
    assert resume.status_code == 409
    assert {effect["status"] for effect in effects} == {"PRESERVED", "ROLLED_BACK"}
    assert {event["event_type"] for event in events} >= {
        "TASK_CANCELLED",
        "ROLLBACK_STARTED",
        "ROLLBACK_FINISHED",
        "TASK_FINISHED",
    }
    assert any(event["status"] == "PRESERVED" for event in events)


def test_demo_fixtures_are_unavailable_without_explicit_enablement() -> None:
    app = create_app(Settings.model_validate({"runtime_mode": "offline"}))

    with TestClient(app) as client:
        response = client.post("/api/demo/selective-rollback")

    assert response.status_code == 404
