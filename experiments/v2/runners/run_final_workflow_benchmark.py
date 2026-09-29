"""Run frozen long-horizon approval-aware TaskGraph benchmark fixtures."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import quantiles
from time import perf_counter_ns
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from ra_agent.audit import InMemoryAuditRecorder  # noqa: E402
from ra_agent.contracts import (  # noqa: E402
    ExecutionStatus,
    ExperimentMode,
    SourceType,
    TaskGraph,
    TaskNode,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.runtime.graph_scheduler import RuntimeTaskGraphScheduler  # noqa: E402

FIXTURE_VERSION = "final-workflow-freeze-20260823"
APPROVAL_DELAY_MS = 120
MAX_PARALLELISM = 3


@dataclass(slots=True)
class Timing:
    started_ns: int
    finished_ns: int


@dataclass(slots=True)
class ControlledWorkflowRuntime:
    nodes: dict[str, dict[str, Any]]
    mode: ExperimentMode
    timings: dict[str, Timing] = field(default_factory=dict)
    approvals: dict[str, int] = field(default_factory=dict)

    async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
        node = self.nodes[request.step_id]
        if self._requires_approval(node):
            self.approvals[request.step_id] = perf_counter_ns()
            return ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=ExecutionStatus.WAITING_APPROVAL,
                output={"approval_id": f"approval-{request.request_id}"},
            )
        return await self._execute(request)

    async def resume_after_approval(
        self, request: ToolCallRequest, approval_id: str
    ) -> ToolExecutionResult:
        if approval_id != f"approval-{request.request_id}":
            raise ValueError("approval does not match controlled workflow request")
        return await self._execute(request)

    async def cancel_task(self, task_id: str) -> None:
        return None

    def _requires_approval(self, node: dict[str, Any]) -> bool:
        return (
            self.mode is ExperimentMode.FULL_GUARD
            and node["mutation"]
            or (self.mode is ExperimentMode.ADAPTIVE_RUNTIME and node["high_risk"])
        )

    async def _execute(self, request: ToolCallRequest) -> ToolExecutionResult:
        started_ns = perf_counter_ns()
        await asyncio.sleep(0.006)
        self.timings[request.step_id] = Timing(started_ns, perf_counter_ns())
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.COMMITTED,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )


def _git_commit() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def _git_dirty() -> bool:
    return bool(
        subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            encoding="utf-8",
            errors="replace",
        ).strip()
    )


def _fixture(node_count: int, high_fraction: float) -> list[dict[str, Any]]:
    high_count = max(1, round(node_count * high_fraction))
    high_indexes = set(range(1, node_count, max(1, node_count // high_count)))
    high_indexes = set(sorted(high_indexes)[:high_count])
    nodes: list[dict[str, Any]] = []
    for index in range(node_count):
        dependencies = [f"n-{index - 1}"] if index > 0 and index % 4 == 0 else []
        high_risk = index in high_indexes
        nodes.append(
            {
                "node_id": f"n-{index}",
                "tool_name": "delete_file" if high_risk else "write_file",
                "path": f"protected/{index}.txt" if high_risk else f"safe/{index}.txt",
                "dependencies": dependencies,
                "high_risk": high_risk,
                "mutation": True,
            }
        )
    return nodes


def _request(task_id: str, node: dict[str, Any]) -> ToolCallRequest:
    return ToolCallRequest(
        task_id=task_id,
        step_id=node["node_id"],
        request_id=f"{task_id}-{node['node_id']}",
        tool_name=node["tool_name"],
        arguments={"path": node["path"], "content": node["node_id"]},
        objective="frozen long-horizon approval-aware workflow",
        context_summary="controlled side-effect-free TaskGraph benchmark",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def _graph(
    task_id: str, graph_id: str, nodes: list[dict[str, Any]], max_parallelism: int
) -> TaskGraph:
    return TaskGraph(
        task_id=task_id,
        graph_id=graph_id,
        max_parallelism=max_parallelism,
        nodes=[
            TaskNode(
                task_id=task_id,
                graph_id=graph_id,
                node_id=node["node_id"],
                request=_request(task_id, node),
                dependencies=node["dependencies"],
                parallel_safe=True,
            )
            for node in nodes
        ],
    )


def _max_concurrency(timings: dict[str, Timing]) -> int:
    points = [(timing.started_ns, 1) for timing in timings.values()] + [
        (timing.finished_ns, -1) for timing in timings.values()
    ]
    active = maximum = 0
    for _, change in sorted(points, key=lambda item: (item[0], item[1])):
        active += change
        maximum = max(maximum, active)
    return maximum


def _dependency_violation(
    nodes: list[dict[str, Any]], timings: dict[str, Timing]
) -> bool:
    return any(
        timings[node["node_id"]].started_ns < timings[dependency].finished_ns
        for node in nodes
        for dependency in node["dependencies"]
        if node["node_id"] in timings and dependency in timings
    )


async def _run_off(
    graph: TaskGraph, runtime: ControlledWorkflowRuntime
) -> dict[str, ToolExecutionResult]:
    remaining = {node.node_id: node for node in graph.nodes}
    results: dict[str, ToolExecutionResult] = {}
    while remaining:
        ready = [
            node
            for node in remaining.values()
            if all(dep in results for dep in node.dependencies)
        ]
        if not ready:
            raise RuntimeError("baseline graph has no ready node")
        batch = sorted(ready, key=lambda node: node.node_id)[: graph.max_parallelism]
        completed = await asyncio.gather(
            *(runtime._execute(node.request) for node in batch)
        )
        for node, result in zip(batch, completed, strict=True):
            results[node.node_id] = result
            remaining.pop(node.node_id)
    return results


async def _run_one(
    node_count: int, high_fraction: float, mode: ExperimentMode, repetition: int
) -> dict[str, Any]:
    nodes = _fixture(node_count, high_fraction)
    task_id = f"final-{node_count}-{high_fraction}-{mode.value.lower()}-{repetition}"
    graph_parallelism = 1 if mode is ExperimentMode.FULL_GUARD else MAX_PARALLELISM
    graph = _graph(task_id, f"graph-{task_id}", nodes, graph_parallelism)
    runtime = ControlledWorkflowRuntime({node["node_id"]: node for node in nodes}, mode)
    started_ns = perf_counter_ns()
    waiting_events = 0
    nodes_completed_during_wait = 0
    approval_wait_ms = 0.0
    inferred_conflicts: dict[str, Any] = {}
    if mode is ExperimentMode.BASELINE:
        results = await _run_off(graph, runtime)
        blocked_nodes: dict[str, str] = {}
    else:
        recorder = InMemoryAuditRecorder()
        scheduler = RuntimeTaskGraphScheduler(
            runtime_scheduler=runtime,
            audit_recorder=recorder,
            pause_on_waiting_approval=mode is ExperimentMode.FULL_GUARD,
        )
        result = await scheduler.schedule_graph(graph)
        while result.blocked_nodes:
            waiting = [
                node_id
                for node_id, reason in result.blocked_nodes.items()
                if reason == "WAITING_APPROVAL"
            ]
            if not waiting:
                raise RuntimeError(
                    f"non-approval blocked nodes: {result.blocked_nodes}"
                )
            node_id = sorted(waiting)[0]
            waiting_events += 1
            approval_started_ns = runtime.approvals[node_id]
            await asyncio.sleep(APPROVAL_DELAY_MS / 1000)
            approval_decision_ns = perf_counter_ns()
            approval_wait_ms += (approval_decision_ns - approval_started_ns) / 1_000_000
            nodes_completed_during_wait += sum(
                timing.finished_ns <= approval_decision_ns
                and timing.finished_ns >= approval_started_ns
                for candidate, timing in runtime.timings.items()
                if candidate != node_id
            )
            result = await scheduler.resume_after_approval(
                graph.graph_id, f"approval-{task_id}-{node_id}"
            )
        results = result.node_results
        blocked_nodes = result.blocked_nodes
        inferred_conflicts = result.inferred_conflicts
    elapsed_ms = (perf_counter_ns() - started_ns) / 1_000_000
    completion = len(results) == node_count and all(
        result.status is ExecutionStatus.COMMITTED for result in results.values()
    )
    max_concurrency = _max_concurrency(runtime.timings)
    dependency_violation = _dependency_violation(nodes, runtime.timings)
    conflict_violation = bool(inferred_conflicts) and max_concurrency > 1
    high_risk_count = sum(node["high_risk"] for node in nodes)
    mutation_count = len(nodes)
    return {
        "schema_version": "final-workflow-1",
        "timestamp": datetime.now(UTC).isoformat(),
        "fixture_version": FIXTURE_VERSION,
        "git_commit": _git_commit(),
        "git_dirty": _git_dirty(),
        "environment": {"python": platform.python_version(), "os": platform.platform()},
        "evidence_set": "long_horizon_workflow",
        "mode": mode.value,
        "repetition": repetition,
        "node_count": node_count,
        "high_risk_fraction": high_fraction,
        "high_risk_count": high_risk_count,
        "mutation_count": mutation_count,
        "approval_actions": waiting_events,
        "approval_waiting_events": waiting_events,
        "approval_wait_ms": approval_wait_ms,
        "automatically_allowed_safe_actions": sum(
            not node["high_risk"] for node in nodes
        )
        if mode is ExperimentMode.ADAPTIVE_RUNTIME
        else 0,
        "nodes_completed_during_approval": nodes_completed_during_wait,
        "blocked_node_count": len(blocked_nodes),
        "completion": completion,
        "total_completion_ms": elapsed_ms,
        "max_parallelism": MAX_PARALLELISM,
        "actual_safe_peak_parallelism": max_concurrency,
        "theoretical_safe_parallelism": MAX_PARALLELISM,
        "safe_parallelism_utilization": max_concurrency / MAX_PARALLELISM,
        "dependency_violation_count": int(dependency_violation),
        "conflict_violation_count": int(conflict_violation),
        "unsafe_side_effect_count": 0,
        "inferred_conflicts": inferred_conflicts,
        "final_statuses": {
            node_id: result.status.value for node_id, result in results.items()
        },
        "error_code": None,
    }


def _percentile(values: list[float], percentile: int) -> float:
    return (
        quantiles(values, n=100, method="inclusive")[percentile - 1]
        if len(values) > 1
        else values[0]
    )


def _write(rows: list[dict[str, Any]], output: Path) -> None:
    raw, derived = output / "raw", output / "derived"
    raw.mkdir(parents=True, exist_ok=True)
    derived.mkdir(parents=True, exist_ok=True)
    (raw / "final-workflow.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    fields = sorted({field for row in rows for field in row})
    with (raw / "final-workflow.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            {
                key: json.dumps(value) if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            }
            for row in rows
        )
    summary: list[dict[str, Any]] = []
    for key in sorted(
        {(row["mode"], row["node_count"], row["high_risk_fraction"]) for row in rows}
    ):
        group = [
            row
            for row in rows
            if (row["mode"], row["node_count"], row["high_risk_fraction"]) == key
        ]
        latency = [row["total_completion_ms"] for row in group]
        summary.append(
            {
                "mode": key[0],
                "node_count": key[1],
                "high_risk_fraction": key[2],
                "runs": len(group),
                "median_completion_ms": _percentile(latency, 50),
                "iqr_completion_ms": _percentile(latency, 75)
                - _percentile(latency, 25),
                "p95_completion_ms": _percentile(latency, 95),
                "mean_approval_wait_ms": sum(row["approval_wait_ms"] for row in group)
                / len(group),
                "mean_approval_actions": sum(row["approval_actions"] for row in group)
                / len(group),
                "mean_safe_parallelism_utilization": sum(
                    row["safe_parallelism_utilization"] for row in group
                )
                / len(group),
                "completion_rate": sum(row["completion"] for row in group) / len(group),
                "conflict_violation_rate": sum(
                    row["conflict_violation_count"] for row in group
                )
                / len(group),
                "dependency_violation_rate": sum(
                    row["dependency_violation_count"] for row in group
                )
                / len(group),
            }
        )
    with (derived / "final-workflow-summary.csv").open(
        "w", encoding="utf-8", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary[0]))
        writer.writeheader()
        writer.writerows(summary)
    (derived / "final-workflow-manifest.json").write_text(
        json.dumps(
            {
                "fixture_version": FIXTURE_VERSION,
                "raw_row_count": len(rows),
                "approval_delay_ms": APPROVAL_DELAY_MS,
                "max_parallelism": MAX_PARALLELISM,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


async def _run(output: Path, repetitions: int) -> None:
    rows: list[dict[str, Any]] = []
    for node_count in (5, 10, 20):
        for high_fraction in (0.2, 0.4):
            for mode in ExperimentMode:
                await _run_one(node_count, high_fraction, mode, 0)
                for repetition in range(1, repetitions + 1):
                    rows.append(
                        await _run_one(node_count, high_fraction, mode, repetition)
                    )
    _write(rows, output)
    print(f"wrote {len(rows)} final workflow rows")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--repetitions", type=int, default=10)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    asyncio.run(_run(args.output_directory, args.repetitions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
