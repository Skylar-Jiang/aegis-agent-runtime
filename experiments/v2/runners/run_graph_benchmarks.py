"""Run deterministic, side-effect-free V2 TaskGraph benchmarks.

The runner exercises RuntimeTaskGraphScheduler and its Audit events.  Its
controlled runtime adapter performs no filesystem or external side effect; it
only supplies bounded fixture latency and records monotonic node timing facts.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import platform
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from ra_agent.audit import InMemoryAuditRecorder
from ra_agent.contracts import (
    ExecutionStatus,
    ExperimentMode,
    ExperimentResult,
    SourceType,
    TaskGraph,
    TaskGraphResult,
    TaskNode,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.runtime import RuntimeTaskGraphScheduler


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "experiments" / "v2" / "fixtures" / "graph_benchmarks.json"


@dataclass(slots=True)
class NodeTiming:
    started_ns: int
    finished_ns: int


@dataclass(slots=True)
class ControlledRuntime:
    fixture: dict[str, Any]
    mode: ExperimentMode
    timings: dict[str, NodeTiming] = field(default_factory=dict)
    approval_requested_ns: int | None = None
    approval_resumed: bool = False

    async def schedule(self, request: ToolCallRequest) -> ToolExecutionResult:
        node = self._node(request.step_id)
        if node.get("requires_approval") and self.mode is not ExperimentMode.BASELINE:
            self.approval_requested_ns = perf_counter_ns()
            return ToolExecutionResult(
                task_id=request.task_id,
                step_id=request.step_id,
                request_id=request.request_id,
                status=ExecutionStatus.WAITING_APPROVAL,
                output={"approval_id": f"approval-{request.request_id}"},
            )
        return await self._execute(request, node)

    async def resume_after_approval(
        self, request: ToolCallRequest, approval_id: str
    ) -> ToolExecutionResult:
        if approval_id != f"approval-{request.request_id}":
            raise ValueError("approval does not match controlled fixture")
        self.approval_resumed = True
        return await self._execute(request, self._node(request.step_id))

    async def cancel_task(self, task_id: str) -> None:
        return None

    def _node(self, node_id: str) -> dict[str, Any]:
        return next(node for node in self.fixture["nodes"] if node["node_id"] == node_id)

    async def _execute(
        self, request: ToolCallRequest, node: dict[str, Any]
    ) -> ToolExecutionResult:
        started_ns = perf_counter_ns()
        await asyncio.sleep(int(node["duration_ms"]) / 1000)
        finished_ns = perf_counter_ns()
        self.timings[request.step_id] = NodeTiming(started_ns, finished_ns)
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.COMMITTED,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _request(task_id: str, graph_id: str, node: dict[str, Any]) -> ToolCallRequest:
    return ToolCallRequest(
        task_id=task_id,
        step_id=node["node_id"],
        request_id=f"{task_id}-{node['node_id']}",
        tool_name=node["tool_name"],
        arguments={"path": node.get("path", f"fixtures/{node['node_id']}.txt")},
        objective="deterministic V2 graph benchmark fixture",
        context_summary="bounded side-effect-free graph benchmark",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
    )


def _graph(fixture_id: str, fixture: dict[str, Any], mode: ExperimentMode, repetition: int) -> TaskGraph:
    task_id = f"benchmark-{fixture_id}-{mode.value.lower()}-{repetition}"
    graph_id = f"graph-{fixture_id}-{mode.value.lower()}-{repetition}"
    max_parallelism = 1 if mode is not ExperimentMode.ADAPTIVE_RUNTIME else 3
    return TaskGraph(
        graph_id=graph_id,
        task_id=task_id,
        max_parallelism=max_parallelism,
        nodes=[
            TaskNode(
                task_id=task_id,
                graph_id=graph_id,
                node_id=node["node_id"],
                request=_request(task_id, graph_id, node),
                dependencies=list(node.get("dependencies", [])),
                parallel_safe=bool(node["parallel_safe"]),
                effect_targets=list(node.get("effect_targets", [])),
            )
            for node in fixture["nodes"]
        ],
    )


def _max_concurrency(timings: dict[str, NodeTiming]) -> int:
    points = [
        (timing.started_ns, 1)
        for timing in timings.values()
    ] + [
        (timing.finished_ns, -1)
        for timing in timings.values()
    ]
    active = maximum = 0
    for _, delta in sorted(points, key=lambda item: (item[0], item[1])):
        active += delta
        maximum = max(maximum, active)
    return maximum


def _critical_path_ms(graph: TaskGraph, timings: dict[str, NodeTiming]) -> int:
    elapsed = {
        node_id: max(0, (timing.finished_ns - timing.started_ns) // 1_000_000)
        for node_id, timing in timings.items()
    }
    distances: dict[str, int] = {}
    remaining = {node.node_id: set(node.dependencies) for node in graph.nodes}
    while remaining:
        ready = [node_id for node_id, dependencies in remaining.items() if not dependencies]
        if not ready:
            raise ValueError("fixture graph is cyclic")
        for node_id in ready:
            node = next(item for item in graph.nodes if item.node_id == node_id)
            distances[node_id] = elapsed.get(node_id, 0) + max(
                (distances[dependency] for dependency in node.dependencies), default=0
            )
            remaining.pop(node_id)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)
    return max(distances.values(), default=0)


def _assert_runtime_facts(
    *,
    graph: TaskGraph,
    result: TaskGraphResult,
    recorder: InMemoryAuditRecorder,
    timings: dict[str, NodeTiming],
    fixture_id: str,
    mode: ExperimentMode,
    approval_decision_ns: int | None,
) -> None:
    if not all(
        node_result.status is ExecutionStatus.COMMITTED
        for node_result in result.node_results.values()
    ):
        raise RuntimeError("benchmark graph contains a non-committed node")
    events = recorder.events_for(graph.task_id)
    if not events or any(event.details.get("graph_id") != graph.graph_id for event in events):
        raise RuntimeError("benchmark graph audit facts are incomplete")
    if fixture_id == "parallel-dag":
        write_a = timings["write-a"]
        conflicting_write = timings["write-a-conflict"]
        if not (
            write_a.finished_ns <= conflicting_write.started_ns
            or conflicting_write.finished_ns <= write_a.started_ns
        ):
            raise RuntimeError("same effect target did not serialize")
        if timings["serial-read"].started_ns < timings["read-b"].finished_ns:
            raise RuntimeError("dependency did not serialize")
    if fixture_id == "approval-wait" and approval_decision_ns is not None:
        if mode is ExperimentMode.FULL_GUARD and (
            timings["B"].started_ns < approval_decision_ns
        ):
            raise RuntimeError("global pause allowed an independent node during approval")
        if mode is ExperimentMode.ADAPTIVE_RUNTIME and not (
            timings["B"].finished_ns <= approval_decision_ns
        ):
            raise RuntimeError("approval-aware scheduler did not progress the independent node")


async def _run_one(
    fixture_id: str,
    fixture: dict[str, Any],
    mode: ExperimentMode,
    repetition: int,
    raw_result_path: str,
    runner_command: str,
) -> ExperimentResult:
    graph = _graph(fixture_id, fixture, mode, repetition)
    recorder = InMemoryAuditRecorder()
    runtime = ControlledRuntime(fixture, mode)
    scheduler = RuntimeTaskGraphScheduler(
        runtime_scheduler=runtime,
        audit_recorder=recorder,
        pause_on_waiting_approval=mode is ExperimentMode.FULL_GUARD,
    )
    started_at = datetime.now(UTC)
    graph_started_ns = perf_counter_ns()
    result = await scheduler.schedule_graph(graph)
    approval_wait_ms = 0
    approval_decision_ns: int | None = None
    if result.blocked_nodes.get("A") == "WAITING_APPROVAL":
        assert runtime.approval_requested_ns is not None
        delay_ns = int(fixture["approval_delay_ms"]) * 1_000_000
        remaining_ns = max(0, delay_ns - (perf_counter_ns() - runtime.approval_requested_ns))
        if remaining_ns:
            await asyncio.sleep(remaining_ns / 1_000_000_000)
        approval_decision_ns = perf_counter_ns()
        approval_wait_ms = max(
            0, (approval_decision_ns - runtime.approval_requested_ns) // 1_000_000
        )
        result = await scheduler.resume_after_approval(
            graph.graph_id, f"approval-{graph.task_id}-A"
        )
    graph_finished_ns = perf_counter_ns()
    finished_at = datetime.now(UTC)
    if result.finished_at is None or result.blocked_nodes:
        raise RuntimeError("benchmark graph did not reach a clean terminal state")
    _assert_runtime_facts(
        graph=graph,
        result=result,
        recorder=recorder,
        timings=runtime.timings,
        fixture_id=fixture_id,
        mode=mode,
        approval_decision_ns=approval_decision_ns,
    )
    sum_node_elapsed_ms = sum(
        max(0, (timing.finished_ns - timing.started_ns) // 1_000_000)
        for timing in runtime.timings.values()
    )
    approval_started_ns = runtime.approval_requested_ns
    approval_finished_ns = approval_decision_ns if approval_started_ns else None
    completed_during_approval = [
        timing
        for node_id, timing in runtime.timings.items()
        if node_id != "A"
        and approval_started_ns is not None
        and approval_finished_ns is not None
        and approval_started_ns <= timing.finished_ns <= approval_finished_ns
    ]
    hidden_approval_wait_ms = sum(
        max(0, (timing.finished_ns - timing.started_ns) // 1_000_000)
        for timing in completed_during_approval
    )
    audit_events = recorder.events_for(graph.task_id)
    digest = hashlib.sha256(
        "|".join(f"{event.event_type.value}:{event.status}" for event in audit_events).encode()
    ).hexdigest()
    graph_elapsed_ms = max(0, (graph_finished_ns - graph_started_ns) // 1_000_000)
    return ExperimentResult(
        schema_version="0.4",
        run_id=f"{fixture_id}-{mode.value.lower()}-{repetition}",
        case_id=fixture_id,
        repetition=repetition,
        mode=mode,
        graph_id=graph.graph_id,
        task_id=graph.task_id,
        started_at=started_at,
        finished_at=finished_at,
        git_commit=_git_commit(),
        python_version=sys.version.split()[0],
        node_version="N/A (backend graph runner)",
        os=platform.platform(),
        environment_fingerprint=hashlib.sha256(
            json.dumps(fixture, sort_keys=True).encode()
        ).hexdigest(),
        runner_command=runner_command,
        fixture_id=fixture_id,
        objective_class=fixture["objective_class"],
        node_count=len(graph.nodes),
        dependency_edge_count=sum(len(node.dependencies) for node in graph.nodes),
        max_parallelism=graph.max_parallelism,
        tool_sequence=[node.request.tool_name for node in graph.nodes],
        elapsed_ms=graph_elapsed_ms,
        graph_elapsed_ms=graph_elapsed_ms,
        critical_path_ms=_critical_path_ms(graph, runtime.timings),
        parallel_saved_ms=max(0, sum_node_elapsed_ms - graph_elapsed_ms),
        approval_wait_ms=approval_wait_ms,
        rollback_elapsed_ms=0,
        status="COMPLETED",
        expected_status="COMPLETED",
        safety_outcome="SAFE_ALLOWED",
        tool_executed_count=len(runtime.timings),
        unsafe_tool_executed_count=0,
        blocked_count=0,
        false_block_count=0,
        risk_escalation_count=0,
        check_count=0,
        audit_event_count=len(audit_events),
        approval_requested_count=int(approval_started_ns is not None),
        approval_decision_count=int(approval_started_ns is not None),
        manual_action_count=int(approval_started_ns is not None),
        checkpoint_count=0,
        pending_effect_count=0,
        commit_count=len(runtime.timings),
        rollback_count=0,
        selective_rollback_count=0,
        residual_effect_count=0,
        metrics={
            "sum_node_elapsed_ms": sum_node_elapsed_ms,
            "max_observed_concurrency": _max_concurrency(runtime.timings),
            "nodes_completed_during_approval": len(completed_during_approval),
            "hidden_approval_wait_ms": hidden_approval_wait_ms,
            "affected_node_count": 0,
            "rolled_back_effect_count": 0,
            "preserved_node_count": 0,
            "preserved_effect_count": 0,
        },
        audit_digest=digest,
        raw_result_path=raw_result_path,
        error_code=None,
        notes=(
            "Controlled side-effect-free RuntimeTaskGraphScheduler benchmark; "
            "node and approval timing are perf_counter_ns facts."
        ),
    )


async def run_benchmarks(
    *, output_directory: Path, output_stem: str, repetitions: int, runner_command: str
) -> list[ExperimentResult]:
    fixtures = json.loads(FIXTURES.read_text(encoding="utf-8"))
    raw_path = output_directory / f"{output_stem}.jsonl"
    results: list[ExperimentResult] = []
    for fixture_id, fixture in fixtures.items():
        for repetition in range(1, repetitions + 1):
            for mode in ExperimentMode:
                results.append(
                    await _run_one(
                        fixture_id,
                        fixture,
                        mode,
                        repetition,
                        raw_path.as_posix(),
                        runner_command,
                    )
                )
    return results


def _write_results(results: list[ExperimentResult], output_directory: Path, output_stem: str) -> None:
    output_directory.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_directory / f"{output_stem}.jsonl"
    csv_path = output_directory / f"{output_stem}.csv"
    jsonl_path.write_text(
        "".join(result.model_dump_json() + "\n" for result in results), encoding="utf-8"
    )
    rows = [result.model_dump(mode="json") for result in results]
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    grouped: dict[str, list[ExperimentResult]] = defaultdict(list)
    for result in results:
        grouped[f"{result.fixture_id}:{result.mode.value}"].append(result)
    summary_path = output_directory.parent / "derived" / f"{output_stem}-summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {
                "source_row_count": len(results),
                "source_jsonl": jsonl_path.as_posix(),
                "by_fixture_mode": {
                    key: {
                        "sample_count": len(group),
                        "mean_graph_elapsed_ms": round(
                            sum(row.graph_elapsed_ms for row in group) / len(group), 2
                        ),
                        "mean_parallel_saved_ms": round(
                            sum(row.parallel_saved_ms for row in group) / len(group), 2
                        ),
                        "mean_approval_wait_ms": round(
                            sum(row.approval_wait_ms for row in group) / len(group), 2
                        ),
                        "mean_nodes_completed_during_approval": round(
                            sum(
                                row.metrics["nodes_completed_during_approval"]
                                for row in group
                            )
                            / len(group),
                            2,
                        ),
                    }
                    for key, group in sorted(grouped.items())
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--output-stem", required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.repetitions <= 0:
        raise SystemExit("repetitions must be positive")
    command = " ".join(sys.argv)
    results = asyncio.run(
        run_benchmarks(
            output_directory=args.output_directory,
            output_stem=args.output_stem,
            repetitions=args.repetitions,
            runner_command=command,
        )
    )
    _write_results(results, args.output_directory, args.output_stem)
    print(f"wrote {len(results)} contract-valid rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
