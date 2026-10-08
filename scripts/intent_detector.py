"""Offline member-3 detector training, prefix replay and bounded measurements.

The replay format is private to this script, not the frozen runtime API or the
member-2 evaluation dataset. Recorded observations are replayed regardless of a
proposed decision; no tools execute and no real side-effect prevention is proved.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import platform
import statistics
import sys
import time
import tracemalloc
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from ra_agent.intent.detection._types import (  # noqa: E402
    CandidateAction,
    ContractSnapshot,
    DetectionInput,
    ObservedBehavior,
    SourceRecord,
)
from ra_agent.intent.detection.engine import IntentDetectorCore  # noqa: E402
from ra_agent.intent.detection.policy import DetectorPolicy  # noqa: E402
from ra_agent.intent.detection.semantic import (  # noqa: E402
    LightweightSemanticModel,
    train_linear_model,
)

MODES = ("rules", "single", "full", "no-sources")
SCOPE = {
    "component": "member-3 private detector core",
    "runtime_integration": "not exercised; frozen member-1 adapter requires joint validation",
    "tool_execution": False,
    "actual_side_effect_prevention_measured": False,
    "full_prototype_memory_measured": False,
    "official_500_mb_compliance_established": False,
}
_TUPLE_FIELDS = {
    "allowed_tools",
    "allowed_actions",
    "forbidden_actions",
    "resource_limits",
    "goal_targets",
    "authorized_recipients",
    "success_criteria",
    "source_refs",
    "additional_targets",
    "parent_refs",
}


def source_manifest() -> dict[str, str]:
    """Identify the actual working sources, including uncommitted detector files."""
    paths = sorted((ROOT / "backend/src/ra_agent/intent/detection").glob("*.py"))
    paths.append(Path(__file__))
    return {
        path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    with path.open("rb") as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError("input exceeds the 16 MiB replay/training file limit")

    def invalid_number(value: str) -> None:
        raise ValueError(f"non-finite JSON number: {value}")

    return json.loads(
        raw, object_pairs_hook=_unique_object, parse_constant=invalid_number
    )


def write_json(value: Any, path: Path | None = None) -> None:
    serialized = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if path is None:
        print(serialized, end="")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(serialized, encoding="utf-8")


def policy_for_mode(mode: str, **limits: Any) -> DetectorPolicy:
    if mode not in MODES:
        raise ValueError("unknown replay mode")
    return DetectorPolicy(
        version=f"offline-{mode}-v1",
        enable_semantic=mode != "rules",
        enable_sequence=mode in {"full", "no-sources"},
        enable_sources=mode in {"single", "full"},
        **limits,
    )


def _record(record_type: Any, value: Any) -> Any:
    if not isinstance(value, dict):
        raise ValueError(f"{record_type.__name__} must be an object")
    valid = {field.name for field in fields(record_type)}
    if set(value) - valid:
        raise ValueError(
            f"unexpected {record_type.__name__} fields: {set(value) - valid}"
        )
    converted = dict(value)
    for name in _TUPLE_FIELDS & value.keys():
        if value[name] is not None:
            if not isinstance(value[name], list) or any(
                not isinstance(item, str) for item in value[name]
            ):
                raise ValueError(f"{name} must be a list of strings")
            converted[name] = tuple(value[name])
    return record_type(**converted)


def run_train(data: Any, **options: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("split") != "train":
        raise ValueError("training input must explicitly declare split='train'")
    rows = data.get("examples")
    if not isinstance(rows, list):
        raise ValueError("training examples must be a list")
    # The model also checks each row; an envelope cannot relabel test rows as train.
    return train_linear_model(rows, **options)


def run_replay(
    data: Any,
    *,
    mode: str = "full",
    model_path: Path | None = None,
    core: IntentDetectorCore | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("replay requires private schema_version=1")
    if data.get("dataset_kind") not in {"synthetic_smoke", "recorded_private_replay"}:
        raise ValueError(
            "dataset_kind must identify a synthetic or private recorded replay"
        )
    cases = data.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 1000:
        raise ValueError("replay requires 1 to 1000 cases")
    detector = core or IntentDetectorCore(
        policy=policy_for_mode(mode),
        semantic_model=LightweightSemanticModel(model_path),
    )
    results: list[dict[str, Any]] = []
    counts = {
        "tp": 0,
        "fp": 0,
        "tn": 0,
        "fn": 0,
        "expected_matches": 0,
        "expected_comparisons": 0,
    }
    identifiers: set[str] = set()
    for case in cases:
        case_id = case["case_id"]
        if not isinstance(case_id, str) or not case_id or case_id in identifiers:
            raise ValueError("case identifiers must be unique nonempty strings")
        identifiers.add(case_id)
        contract = _record(ContractSnapshot, case["contract"])
        detector.clear()
        history: deque[ObservedBehavior] = deque(maxlen=detector.policy.window_size)
        steps = case["steps"]
        if not isinstance(steps, list) or not 1 <= len(steps) <= 20000:
            raise ValueError("each trajectory requires 1 to 20000 steps")
        previous_step = -1
        for step in steps:
            # Deliberately construct each input from explicit fields. Labels,
            # expected decisions, later actions/results and scenario names never enter it.
            if "contract_update" in step:
                updated = _record(ContractSnapshot, step["contract_update"])
                if (
                    updated.task_id != contract.task_id
                    or not updated.confirmed
                    or updated.version == contract.version
                ):
                    raise ValueError(
                        "fixture contract update needs same task and new confirmed version"
                    )
                contract = updated
            candidate = _record(CandidateAction, step["candidate"])
            if candidate.step_index <= previous_step:
                raise ValueError("candidate step indexes must strictly increase")
            previous_step = candidate.step_index
            sources = tuple(
                _record(SourceRecord, item) for item in step.get("sources", [])
            )
            context = DetectionInput(
                contract=contract,
                candidate=candidate,
                history=tuple(history),
                sources=sources,
                hard_violations=tuple(step.get("hard_violations", [])),
                replan_attempts=step.get("replan_attempts", 0),
                repeated_recovery=step.get("repeated_recovery", False),
                recovery_budget_exhausted=step.get("recovery_budget_exhausted", False),
                now=datetime(2026, 1, 1, tzinfo=UTC)
                + timedelta(seconds=candidate.step_index),
            )
            assessment = detector.analyze(context)
            record = asdict(assessment)
            record["expires_at"] = assessment.expires_at.isoformat()
            record["case_id"] = case_id
            record["step_index"] = candidate.step_index
            # Ground truth is read only after inference and only for offline comparison.
            label = step.get("label")
            if label is not None:
                if type(label) is not bool:
                    raise ValueError("step label must be boolean when provided")
                proposed_intervention = assessment.disposition.value != "CONTINUE"
                key = (
                    ("tp" if label else "fp")
                    if proposed_intervention
                    else ("fn" if label else "tn")
                )
                counts[key] += 1
            expected = step.get("expected_decision")
            if isinstance(expected, dict):
                expected = expected.get(mode)
            if expected is not None:
                counts["expected_comparisons"] += 1
                counts["expected_matches"] += expected == assessment.disposition.value
            record["offline_comparison"] = {
                "label": label,
                "expected_decision": expected,
            }
            results.append(record)
            if "observed" in step:
                observed = _record(ObservedBehavior, step["observed"])
                if (
                    observed.step_index != candidate.step_index
                    or observed.task_id != contract.task_id
                    or observed.contract_version != contract.version
                ):
                    raise ValueError(
                        "recorded result must match this task/version/step"
                    )
                history.append(observed)
    positives, negatives = counts["tp"] + counts["fn"], counts["tn"] + counts["fp"]
    return {
        "command": "replay",
        "source_sha256": source_manifest(),
        "dataset_sha256": hashlib.sha256(
            json.dumps(
                data, ensure_ascii=False, sort_keys=True, allow_nan=False
            ).encode("utf-8")
        ).hexdigest(),
        "schema_version": 1,
        "scope": SCOPE,
        "dataset_kind": data["dataset_kind"],
        "mode": mode,
        "policy": asdict(detector.policy),
        "detector_version": detector.version,
        "semantic_model_version": detector.semantic_model.version,
        "contract_changes": "trusted fixture snapshots only; user authorization not exercised",
        "comparison": {
            **counts,
            "recall": counts["tp"] / positives if positives else None,
            "false_positive_rate": counts["fp"] / negatives if negatives else None,
            "positive_definition": "any proposed non-CONTINUE disposition; not executed blocking",
            "quality_claim": "fixture sanity check; no generalization or official benchmark claim",
        },
        "steps": results,
    }


def process_peak_memory() -> dict[str, Any]:
    """OS process lifetime high water mark, including startup and model loading."""
    if sys.platform == "win32":
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(
            kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb
        ):
            raise OSError(ctypes.get_last_error(), "GetProcessMemoryInfo failed")
        return {
            "bytes": counters.PeakWorkingSetSize,
            "method": "Windows GetProcessMemoryInfo.PeakWorkingSetSize",
            "scope": "process lifetime peak working set, including startup/model loading",
        }
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {
        "bytes": int(peak if sys.platform == "darwin" else peak * 1024),
        "method": "resource.getrusage(RUSAGE_SELF).ru_maxrss",
        "scope": "process lifetime peak RSS, including startup/model loading",
    }


def _latencies(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "calls": len(values),
        "median_ms": statistics.median(values),
        "p95_ms": ordered[max(0, math.ceil(0.95 * len(values)) - 1)],
    }


def run_benchmark(
    *,
    steps: int = 2000,
    tasks: int = 128,
    workers: int = 4,
    max_tasks: int = 32,
    window_size: int = 12,
    cache_size: int = 128,
    model_path: Path | None = None,
) -> dict[str, Any]:
    for name, value, maximum in (
        ("steps", steps, 20000),
        ("tasks", tasks, 1024),
        ("workers", workers, 32),
        ("max_tasks", max_tasks, 1024),
        ("window_size", window_size, 256),
    ):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be in [1, {maximum}]")
    if type(cache_size) is not int or not 0 <= cache_size <= 2048:
        raise ValueError("cache_size must be in [0, 2048]")
    tracing_before = tracemalloc.is_tracing()
    if not tracing_before:
        tracemalloc.start()
    try:
        started = time.perf_counter()
        model = LightweightSemanticModel(model_path, cache_size=cache_size)
        load_ms = (time.perf_counter() - started) * 1000
        policy = policy_for_mode(
            "full", max_tasks=max_tasks, window_size=window_size, cache_size=cache_size
        )
        detector = IntentDetectorCore(policy=policy, semantic_model=model)

        def trajectory(task_id: str, count: int) -> list[float]:
            contract = ContractSnapshot(
                task_id,
                f"contract:{task_id}",
                "v1",
                "分析设备 A 配置并生成风险报告",
                f"contract-ref:{task_id}",
                goal_targets=("devices/A/",),
            )
            history: deque[ObservedBehavior] = deque(maxlen=window_size)
            durations: list[float] = []
            for index in range(count):
                action = CandidateAction(
                    f"{task_id}:{index}",
                    index,
                    "read_config",
                    "read",
                    "devices/A/config",
                    f"action:{task_id}:{index}",
                    subgoal="分析设备 A 配置风险",
                    parameters_text=f"request={task_id}:{index}",
                )
                context = DetectionInput(contract, action, tuple(history))
                tick = time.perf_counter()
                detector.analyze(context)
                durations.append((time.perf_counter() - tick) * 1000)
                history.append(
                    ObservedBehavior(
                        f"event:{task_id}:{index}",
                        task_id,
                        "v1",
                        index,
                        action.tool,
                        action.action,
                        action.target,
                        f"result:{task_id}:{index}",
                        verified_progress=True,
                    )
                )
            return durations

        single = trajectory("single", steps)
        single_sizes = detector.state_sizes
        detector.clear()
        actual_tasks = min(tasks, steps)
        job_sizes = [
            steps // actual_tasks + (i < steps % actual_tasks)
            for i in range(actual_tasks)
        ]
        wall = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            batches = list(
                pool.map(
                    lambda job: trajectory(f"multi-{job[0]}", job[1]),
                    enumerate(job_sizes),
                )
            )
        multi_wall_ms = (time.perf_counter() - wall) * 1000
        concurrent = [value for batch in batches for value in batch]
        sizes = detector.state_sizes
        bounds = {
            "tasks": max_tasks,
            "events": max_tasks * window_size,
            "seen_events": max_tasks * max(32, window_size * 4),
            "cache_entries": cache_size,
        }
        _, python_peak = tracemalloc.get_traced_memory()
        return {
            "command": "benchmark",
            "source_sha256": source_manifest(),
            "scope": SCOPE,
            "environment": {"python": sys.version, "platform": platform.platform()},
            "semantic_model_version": model.version,
            "detector_version": detector.version,
            "model_load_ms": load_ms,
            "cold_first_analysis_ms": single[0],
            "single_task": {**_latencies(single), "state_sizes": single_sizes},
            "concurrent_tasks": {
                **_latencies(concurrent),
                "tasks": actual_tasks,
                "workers": workers,
                "wall_ms": multi_wall_ms,
                "latency_scope": "analyze call including shared-core lock contention",
                "parallelism": "thread requests; core state processing is serialized by its lock",
            },
            "retained": {**sizes, "cache_entries": model.cache_entries},
            "configured_bounds": bounds,
            "bounds_satisfied": all(sizes[key] <= bounds[key] for key in sizes)
            and model.cache_entries <= cache_size,
            "python_allocation_peak": {
                "bytes": python_peak,
                "method": "tracemalloc.get_traced_memory",
                "scope": "Python allocations during model load and benchmark, including harness",
                "preexisting_trace": tracing_before,
            },
            "process_memory_peak": process_peak_memory(),
            "measurement_notes": [
                "Run CLI in a fresh process for a cold-start process peak.",
                "tracemalloc instrumentation is enabled during timing and adds overhead.",
                "No LLM, gateway, frontend, real tools or full prototype are measured.",
                "Bounds cover retained detector state; harness/input allocations are separate.",
            ],
        }
    finally:
        if not tracing_before:
            tracemalloc.stop()


def main(argv: list[str] | None = None) -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    train = subcommands.add_parser("train", help="explicit offline train-split fit")
    train.add_argument("--input", type=Path, required=True)
    train.add_argument("--output", type=Path, required=True)
    train.add_argument("--epochs", type=int, default=30)
    train.add_argument("--dimensions", type=int, default=4096)
    train.add_argument("--model-version", default="intent-linear-v1")
    replay = subcommands.add_parser(
        "replay", help="private prefix-only recorded trajectory replay"
    )
    replay.add_argument("--input", type=Path, required=True)
    replay.add_argument("--output", type=Path)
    replay.add_argument("--mode", choices=MODES, default="full")
    replay.add_argument("--model", type=Path)
    benchmark = subcommands.add_parser(
        "benchmark", help="detector-only measured resource usage"
    )
    benchmark.add_argument("--output", type=Path)
    benchmark.add_argument("--model", type=Path)
    for name, default in (
        ("steps", 2000),
        ("tasks", 128),
        ("workers", 4),
        ("max-tasks", 32),
        ("window-size", 12),
        ("cache-size", 128),
    ):
        benchmark.add_argument("--" + name, type=int, default=default)
    args = parser.parse_args(argv)
    try:
        if args.command == "train":
            result = run_train(
                load_json(args.input),
                epochs=args.epochs,
                dimensions=args.dimensions,
                model_version=args.model_version,
            )
        elif args.command == "replay":
            result = run_replay(
                load_json(args.input), mode=args.mode, model_path=args.model
            )
        else:
            result = run_benchmark(
                steps=args.steps,
                tasks=args.tasks,
                workers=args.workers,
                max_tasks=args.max_tasks,
                window_size=args.window_size,
                cache_size=args.cache_size,
                model_path=args.model,
            )
        write_json(result, args.output)
        if args.command == "train":
            write_json(
                {
                    "source_sha256": source_manifest(),
                    "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
                    "model_sha256": result["sha256"],
                    "model_version": result["model_version"],
                    "training": result["training"],
                    "scope": "offline fit only; validation and runtime approval remain required",
                },
                args.output.with_suffix(args.output.suffix + ".manifest.json"),
            )
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
