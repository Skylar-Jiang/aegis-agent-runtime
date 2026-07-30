from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter_ns
from typing import Any
from uuid import uuid4


def _discover_repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "backend" / "src" / "ra_agent").is_dir():
            return parent
    raise RuntimeError("cannot locate repository root from rollback benchmark runner")


REPO_ROOT = _discover_repo_root()
BACKEND_SRC = REPO_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from ra_agent.contracts import (  # noqa: E402
    DeepCheckResult,
    EffectStatus,
    ExecutionStatus,
    ExperimentMode,
    ExperimentResult,
    PostCheckResult,
    RollbackPlan,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution.artifacts import (  # noqa: E402
    build_quarantined_download_artifact,
    build_tool_output_artifact,
)
from ra_agent.execution.checkpoint import FilesystemCheckpointManager  # noqa: E402
from ra_agent.execution.commit_gate import FilesystemCommitGate  # noqa: E402
from ra_agent.execution.download_manager import DownloadLifecycleManager  # noqa: E402
from ra_agent.execution.effect_manager import EffectManager  # noqa: E402
from ra_agent.execution.effect_store import FilesystemEffectStore  # noqa: E402
from ra_agent.execution.pending_store import PendingStore  # noqa: E402
from ra_agent.execution.quarantine import (  # noqa: E402
    FilesystemQuarantineStore,
    QuarantineStatus,
)
from ra_agent.execution.rollback import FilesystemRollbackManager  # noqa: E402
from ra_agent.execution.selective_rollback import SelectiveRollbackExecutor  # noqa: E402
from ra_agent.memory import FilesystemMemoryStore, MemoryLifecycleManager  # noqa: E402
from ra_agent.tools.implementations.memory_tools import MemoryWriteHandler  # noqa: E402
from ra_agent.tools.implementations.write_file import WriteFileHandler  # noqa: E402
from ra_agent.tools.path_resolver import SafePathResolver  # noqa: E402


FIXTURE_FILES = {
    "M3-C01": "m3_c01_file_selective.json",
    "M3-C02": "m3_c02_memory_restore.json",
    "M3-C07": "m3_c07_download_isolation.json",
}
SUPPORTED_MODES = frozenset({ExperimentMode.ADAPTIVE_RUNTIME})
RAW_JSONL_NAME = "rollback_benchmark.jsonl"
RAW_CSV_NAME = "rollback_benchmark.csv"


class GroundTruthMismatch(RuntimeError):
    """Raised when observed resource/effect facts differ from the frozen fixture."""


@dataclass(slots=True)
class RunFacts:
    checkpoint_count: int = 0
    pending_effect_count: int = 0
    commit_count: int = 0
    rollback_count: int = 0
    selective_rollback_count: int = 0
    residual_effect_count: int = 0
    rollback_elapsed_ms: int = 0
    tool_executed_count: int = 0
    check_count: int = 0
    affected_node_count: int = 0
    rolled_back_effect_count: int = 0
    preserved_node_count: int = 0
    preserved_effect_count: int = 0
    tool_sequence: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ScenarioObservation:
    status: str
    safety_outcome: str
    notes: str


@dataclass(slots=True)
class FileEnvironment:
    workspace: Path
    pending_store: PendingStore
    checkpoint_manager: FilesystemCheckpointManager
    effect_store: FilesystemEffectStore
    effect_manager: EffectManager
    rollback_manager: FilesystemRollbackManager
    commit_gate: FilesystemCommitGate
    resolver: SafePathResolver


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _elapsed_ms(started_ns: int) -> int:
    return max(0, round((perf_counter_ns() - started_ns) / 1_000_000))


def _run_command(arguments: list[str]) -> str:
    try:
        completed = subprocess.run(
            arguments,
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "N/A"
    return completed.stdout.strip() or completed.stderr.strip() or "N/A"


def _git_commit() -> str:
    value = _run_command(["git", "rev-parse", "HEAD"])
    if value == "N/A":
        raise RuntimeError("git commit is required for formal experiment output")
    return value


def _node_version() -> str:
    return _run_command(["node", "--version"])


def _canonical_runner_command() -> str:
    argv = getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv]
    if os.name == "nt":
        return subprocess.list2cmdline(argv)
    return shlex.join(argv)


def _environment_fingerprint(
    *,
    git_commit: str,
    python_version: str,
    node_version: str,
    operating_system: str,
) -> str:
    canonical = json.dumps(
        {
            "git_commit": git_commit,
            "node_version": node_version,
            "os": operating_system,
            "python_version": python_version,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _relative_to_repo(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _load_fixture(case_id: str) -> dict[str, Any]:
    filename = FIXTURE_FILES[case_id]
    path = REPO_ROOT / "experiments" / "v2" / "fixtures" / "rollback" / filename
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("case_id") != case_id:
        raise ValueError(f"fixture case_id mismatch: {path}")
    return payload


def _request(
    *,
    task_id: str,
    tool_name: str,
    request_id: str,
    arguments: dict[str, object],
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id=task_id,
        step_id=f"step-{request_id}",
        request_id=request_id,
        tool_name=tool_name,
        arguments=arguments,
        objective="formal V2 selective rollback benchmark",
        context_summary="deterministic member 3 rollback fixture",
        source_type=SourceType.AGENT,
        requested_at=_utc_now(),
    )


def _plan(
    *,
    run_id: str,
    task_id: str,
    effect_ids: list[str],
    case_id: str,
) -> RollbackPlan:
    return RollbackPlan(
        plan_id=f"plan-{case_id.lower()}-{run_id}",
        task_id=task_id,
        trigger="benchmark_ground_truth",
        request_ids=[],
        checkpoint_ids=[],
        effect_ids=effect_ids,
        reason=f"formal selective rollback benchmark for {case_id}",
    )


def _assert_equal(label: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise GroundTruthMismatch(
            f"{label} mismatch: expected={expected!r}, actual={actual!r}"
        )


def _make_file_environment(run_root: Path) -> FileEnvironment:
    workspace = run_root / "workspace"
    workspace.mkdir(parents=True)
    resolver = SafePathResolver(
        workspace,
        max_path_length=512,
        max_read_bytes=4096,
        max_write_bytes=4096,
    )
    pending_store = PendingStore(run_root / "pending")
    checkpoint_manager = FilesystemCheckpointManager(
        run_root / "checkpoints",
        resolver,
    )
    effect_store = FilesystemEffectStore(run_root / "effects")
    effect_manager = EffectManager(effect_store)
    rollback_manager = FilesystemRollbackManager(
        resolver,
        pending_store,
        checkpoint_manager,
    )
    commit_gate = FilesystemCommitGate(
        resolver,
        pending_store,
        checkpoint_manager,
        effect_manager,
    )
    return FileEnvironment(
        workspace=workspace,
        pending_store=pending_store,
        checkpoint_manager=checkpoint_manager,
        effect_store=effect_store,
        effect_manager=effect_manager,
        rollback_manager=rollback_manager,
        commit_gate=commit_gate,
        resolver=resolver,
    )


async def _commit_file(
    *,
    environment: FileEnvironment,
    request: ToolCallRequest,
    facts: RunFacts,
) -> str:
    checkpoint = await environment.checkpoint_manager.create(request)
    facts.checkpoint_count += 1

    execution = await WriteFileHandler(
        environment.resolver,
        environment.pending_store,
    )(request)
    facts.tool_executed_count += 1
    facts.tool_sequence.append(request.tool_name)

    await environment.pending_store.bind_checkpoint(
        request.request_id,
        checkpoint.checkpoint_id,
    )
    execution = execution.model_copy(
        update={"checkpoint_id": checkpoint.checkpoint_id}
    )

    effect = await environment.effect_manager.register_pending(request, execution)
    facts.pending_effect_count += 1

    result = await environment.commit_gate.commit(
        execution,
        DeepCheckResult(
            request_id=request.request_id,
            passed=True,
            reason="deterministic benchmark check",
        ),
    )
    facts.check_count += 1
    _assert_equal(
        f"{request.request_id} commit status",
        result.status,
        ExecutionStatus.COMMITTED,
    )
    facts.commit_count += 1

    await environment.pending_store.cleanup(request.request_id)
    return effect.effect_id


async def _execute_selective_rollback(
    *,
    executor: SelectiveRollbackExecutor,
    plan: RollbackPlan,
    facts: RunFacts,
) -> Any:
    facts.selective_rollback_count += 1
    rollback_started_ns = perf_counter_ns()
    try:
        result = await executor.execute(plan)
    finally:
        facts.rollback_elapsed_ms = _elapsed_ms(rollback_started_ns)
    facts.rollback_count = len(result.rolled_back_request_ids) + len(
        result.failed_request_ids
    )
    return result


async def _run_file_case(
    *,
    fixture: dict[str, Any],
    run_id: str,
    task_id: str,
    run_root: Path,
    facts: RunFacts,
) -> ScenarioObservation:
    environment = _make_file_environment(run_root)
    initial_state = fixture["initial_state"]
    for relative_path, content in initial_state.items():
        target = environment.workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    effect_by_request: dict[str, str] = {}
    for write in fixture["writes"]:
        request = _request(
            task_id=task_id,
            tool_name="write_file",
            request_id=write["request_id"],
            arguments={
                "path": write["path"],
                "content": write["content"],
            },
        )
        effect_by_request[request.request_id] = await _commit_file(
            environment=environment,
            request=request,
            facts=facts,
        )

    rollback_effect_ids = [
        effect_by_request[request_id]
        for request_id in fixture["rollback_request_ids"]
    ]
    result = await _execute_selective_rollback(
        executor=SelectiveRollbackExecutor(
            effect_store=environment.effect_store,
            effect_manager=environment.effect_manager,
            checkpoint_manager=environment.checkpoint_manager,
            rollback_manager=environment.rollback_manager,
        ),
        plan=_plan(
            run_id=run_id,
            task_id=task_id,
            effect_ids=rollback_effect_ids,
            case_id=fixture["case_id"],
        ),
        facts=facts,
    )

    ground_truth = fixture["ground_truth"]
    _assert_equal("rollback result status", result.status.value, ground_truth["expected_status"])

    for relative_path, expected_content in ground_truth["final_state"].items():
        actual_content = (environment.workspace / relative_path).read_text(
            encoding="utf-8"
        )
        _assert_equal(
            f"final file {relative_path}",
            actual_content,
            expected_content,
        )

    all_effects = await environment.effect_store.list_by_task_id(task_id)
    status_by_request = {
        effect.request_id: effect.status.value for effect in all_effects
    }
    _assert_equal(
        "effect status map",
        status_by_request,
        ground_truth["effect_status_by_request"],
    )

    facts.rolled_back_effect_count = sum(
        effect.status is EffectStatus.ROLLED_BACK for effect in all_effects
    )
    rollback_requests = set(fixture["rollback_request_ids"])
    facts.preserved_effect_count = sum(
        effect.status is EffectStatus.COMMITTED
        and effect.request_id not in rollback_requests
        for effect in all_effects
    )
    facts.affected_node_count = len(rollback_requests)
    facts.preserved_node_count = facts.preserved_effect_count
    facts.residual_effect_count = sum(
        status_by_request.get(request_id) != expected_status
        for request_id, expected_status in ground_truth[
            "effect_status_by_request"
        ].items()
    )

    _validate_metric_ground_truth(facts, ground_truth)
    return ScenarioObservation(
        status=result.status.value,
        safety_outcome=ground_truth["safety_outcome"],
        notes=(
            "Deterministic local file fixture; TaskGraph timing and Audit are "
            "not applicable in phase A/B."
        ),
    )


async def _commit_memory(
    *,
    memory_store: FilesystemMemoryStore,
    effect_manager: EffectManager,
    lifecycle: MemoryLifecycleManager,
    request: ToolCallRequest,
    facts: RunFacts,
) -> str:
    execution = await MemoryWriteHandler(memory_store)(request)
    facts.tool_executed_count += 1
    facts.tool_sequence.append(request.tool_name)

    effect = await effect_manager.register_pending(request, execution)
    facts.pending_effect_count += 1

    await lifecycle.commit(
        request,
        execution,
        PostCheckResult(
            request_id=request.request_id,
            passed=True,
            reason="deterministic benchmark check",
        ),
    )
    facts.check_count += 1
    facts.commit_count += 1
    return effect.effect_id


async def _run_memory_case(
    *,
    fixture: dict[str, Any],
    run_id: str,
    task_id: str,
    run_root: Path,
    facts: RunFacts,
) -> ScenarioObservation:
    effect_store = FilesystemEffectStore(run_root / "effects")
    effect_manager = EffectManager(effect_store)
    memory_store = FilesystemMemoryStore(
        run_root / "memory",
        max_value_bytes=4096,
    )
    lifecycle = MemoryLifecycleManager(memory_store, effect_manager)

    effect_by_request: dict[str, str] = {}
    for write in fixture["writes"]:
        request = _request(
            task_id=task_id,
            tool_name="memory_write",
            request_id=write["request_id"],
            arguments={"key": write["key"], "value": write["value"]},
        )
        effect_by_request[request.request_id] = await _commit_memory(
            memory_store=memory_store,
            effect_manager=effect_manager,
            lifecycle=lifecycle,
            request=request,
            facts=facts,
        )

    rollback_effect_ids = [
        effect_by_request[request_id]
        for request_id in fixture["rollback_request_ids"]
    ]
    result = await _execute_selective_rollback(
        executor=SelectiveRollbackExecutor(
            effect_store=effect_store,
            effect_manager=effect_manager,
            memory_manager=lifecycle,
        ),
        plan=_plan(
            run_id=run_id,
            task_id=task_id,
            effect_ids=rollback_effect_ids,
            case_id=fixture["case_id"],
        ),
        facts=facts,
    )

    ground_truth = fixture["ground_truth"]
    _assert_equal("rollback result status", result.status.value, ground_truth["expected_status"])

    for key, expected_value in ground_truth["trusted_values"].items():
        trusted = await memory_store.get_trusted_value(key)
        actual_value = None if trusted is None else trusted[1]
        _assert_equal(f"trusted memory {key}", actual_value, expected_value)

    all_effects = await effect_store.list_by_task_id(task_id)
    status_by_request = {
        effect.request_id: effect.status.value for effect in all_effects
    }
    _assert_equal(
        "effect status map",
        status_by_request,
        ground_truth["effect_status_by_request"],
    )

    rollback_requests = set(fixture["rollback_request_ids"])
    facts.rolled_back_effect_count = sum(
        effect.status is EffectStatus.ROLLED_BACK for effect in all_effects
    )
    facts.preserved_effect_count = sum(
        effect.status is EffectStatus.COMMITTED
        and effect.request_id not in rollback_requests
        for effect in all_effects
    )
    facts.affected_node_count = len(rollback_requests)
    facts.preserved_node_count = facts.preserved_effect_count
    facts.residual_effect_count = sum(
        status_by_request.get(request_id) != expected_status
        for request_id, expected_status in ground_truth[
            "effect_status_by_request"
        ].items()
    )

    _validate_metric_ground_truth(facts, ground_truth)
    return ScenarioObservation(
        status=result.status.value,
        safety_outcome=ground_truth["safety_outcome"],
        notes=(
            "Deterministic local Memory fixture; previous TRUSTED version is "
            "read from FilesystemMemoryStore after selective rollback."
        ),
    )


async def _commit_download(
    *,
    quarantine_store: FilesystemQuarantineStore,
    effect_manager: EffectManager,
    lifecycle: DownloadLifecycleManager,
    request: ToolCallRequest,
    payload: bytes,
    source_url: str,
    facts: RunFacts,
) -> str:
    temporary = quarantine_store.create_temporary_path(request.request_id)
    temporary.write_bytes(payload)
    record = await quarantine_store.stage(
        request,
        source_url=source_url,
        final_url=source_url,
        redirect_chain=(),
        temporary_path=temporary,
        content_type="application/octet-stream",
        content_sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        http_status=200,
    )
    facts.tool_executed_count += 1
    facts.tool_sequence.append(request.tool_name)

    output = {
        "downloaded": True,
        "source_url": record.source_url,
        "final_url": record.final_url,
        "content_type": record.content_type,
        "size_bytes": record.size_bytes,
        "sha256": record.content_sha256,
        "status": record.status.value,
    }
    execution = ToolExecutionResult(
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        status=ExecutionStatus.PENDING_COMMIT,
        output=output,
        artifacts=[
            build_tool_output_artifact(
                request,
                output,
                status=ExecutionStatus.PENDING_COMMIT,
            ),
            build_quarantined_download_artifact(
                request,
                quarantine_path=record.quarantine_path,
                source_url=record.source_url,
                final_url=record.final_url,
                content_sha256=record.content_sha256,
                size_bytes=record.size_bytes,
                content_type=record.content_type,
            ),
        ],
        pending_changes=[
            {
                "operation": "DOWNLOAD",
                "quarantine_path": record.quarantine_path,
                "source_url": record.source_url,
                "final_url": record.final_url,
                "content_sha256": record.content_sha256,
                "size_bytes": record.size_bytes,
                "status": record.status.value,
            }
        ],
    )

    effect = await effect_manager.register_pending(request, execution)
    facts.pending_effect_count += 1
    await lifecycle.commit(
        request,
        execution,
        PostCheckResult(
            request_id=request.request_id,
            passed=True,
            reason="deterministic benchmark check",
        ),
    )
    facts.check_count += 1
    facts.commit_count += 1
    return effect.effect_id


async def _run_download_case(
    *,
    fixture: dict[str, Any],
    run_id: str,
    task_id: str,
    run_root: Path,
    facts: RunFacts,
) -> ScenarioObservation:
    effect_store = FilesystemEffectStore(run_root / "effects")
    effect_manager = EffectManager(effect_store)
    quarantine_store = FilesystemQuarantineStore(
        run_root / "quarantine",
        max_download_bytes=4096,
    )
    lifecycle = DownloadLifecycleManager(quarantine_store, effect_manager)

    effect_by_request: dict[str, str] = {}
    for download in fixture["downloads"]:
        request = _request(
            task_id=task_id,
            tool_name="download_url",
            request_id=download["request_id"],
            arguments={"url": download["source_url"]},
        )
        effect_by_request[request.request_id] = await _commit_download(
            quarantine_store=quarantine_store,
            effect_manager=effect_manager,
            lifecycle=lifecycle,
            request=request,
            payload=download["payload_utf8"].encode("utf-8"),
            source_url=download["source_url"],
            facts=facts,
        )

    rollback_effect_ids = [
        effect_by_request[request_id]
        for request_id in fixture["rollback_request_ids"]
    ]
    result = await _execute_selective_rollback(
        executor=SelectiveRollbackExecutor(
            effect_store=effect_store,
            effect_manager=effect_manager,
            download_manager=lifecycle,
        ),
        plan=_plan(
            run_id=run_id,
            task_id=task_id,
            effect_ids=rollback_effect_ids,
            case_id=fixture["case_id"],
        ),
        facts=facts,
    )

    ground_truth = fixture["ground_truth"]
    _assert_equal("rollback result status", result.status.value, ground_truth["expected_status"])

    quarantine_status_by_request = {
        request_id: (await quarantine_store.get(request_id)).status.value
        for request_id in ground_truth["quarantine_status_by_request"]
    }
    _assert_equal(
        "quarantine status map",
        quarantine_status_by_request,
        ground_truth["quarantine_status_by_request"],
    )

    all_effects = await effect_store.list_by_task_id(task_id)
    status_by_request = {
        effect.request_id: effect.status.value for effect in all_effects
    }
    _assert_equal(
        "effect status map",
        status_by_request,
        ground_truth["effect_status_by_request"],
    )

    rollback_requests = set(fixture["rollback_request_ids"])
    facts.rolled_back_effect_count = sum(
        effect.status is EffectStatus.ROLLED_BACK for effect in all_effects
    )
    facts.preserved_effect_count = sum(
        effect.status is EffectStatus.COMMITTED
        and effect.request_id not in rollback_requests
        for effect in all_effects
    )
    facts.affected_node_count = len(rollback_requests)
    facts.preserved_node_count = facts.preserved_effect_count
    facts.residual_effect_count = sum(
        status_by_request.get(request_id) != expected_status
        for request_id, expected_status in ground_truth[
            "effect_status_by_request"
        ].items()
    )

    _validate_metric_ground_truth(facts, ground_truth)
    return ScenarioObservation(
        status=result.status.value,
        safety_outcome=ground_truth["safety_outcome"],
        notes=(
            "Deterministic local quarantine payloads were used; no network "
            "connection or external download occurred."
        ),
    )


def _validate_metric_ground_truth(
    facts: RunFacts,
    ground_truth: dict[str, Any],
) -> None:
    metric_names = (
        "checkpoint_count",
        "pending_effect_count",
        "commit_count",
        "rollback_count",
        "selective_rollback_count",
        "residual_effect_count",
        "affected_node_count",
        "rolled_back_effect_count",
        "preserved_node_count",
        "preserved_effect_count",
    )
    for name in metric_names:
        _assert_equal(name, getattr(facts, name), ground_truth[name])


async def _run_scenario(
    *,
    fixture: dict[str, Any],
    run_id: str,
    task_id: str,
    run_root: Path,
    facts: RunFacts,
) -> ScenarioObservation:
    kind = fixture["resource_kind"]
    if kind == "FILE":
        return await _run_file_case(
            fixture=fixture,
            run_id=run_id,
            task_id=task_id,
            run_root=run_root,
            facts=facts,
        )
    if kind == "MEMORY":
        return await _run_memory_case(
            fixture=fixture,
            run_id=run_id,
            task_id=task_id,
            run_root=run_root,
            facts=facts,
        )
    if kind == "DOWNLOAD":
        return await _run_download_case(
            fixture=fixture,
            run_id=run_id,
            task_id=task_id,
            run_root=run_root,
            facts=facts,
        )
    raise ValueError(f"unsupported rollback fixture resource_kind: {kind}")


def _build_result(
    *,
    fixture: dict[str, Any],
    run_id: str,
    task_id: str,
    mode: ExperimentMode,
    repetition: int,
    started_at: datetime,
    finished_at: datetime,
    elapsed_ms: int,
    facts: RunFacts,
    observation: ScenarioObservation | None,
    error: Exception | None,
    raw_jsonl_path: Path,
    git_commit: str,
    python_version: str,
    node_version: str,
    operating_system: str,
    environment_fingerprint: str,
    runner_command: str,
) -> ExperimentResult:
    ground_truth = fixture["ground_truth"]
    if error is None and observation is not None:
        status = observation.status
        safety_outcome = observation.safety_outcome
        error_code = None
        notes = observation.notes
    else:
        status = ExecutionStatus.FAILED.value
        safety_outcome = "FAIL_GROUND_TRUTH_OR_RUNTIME"
        error_code = type(error).__name__ if error is not None else "UNKNOWN_ERROR"
        notes = (
            f"{type(error).__name__}: {error}"
            if error is not None
            else "scenario returned no observation"
        )

    if node_version == "N/A":
        notes = f"{notes} node_version=N/A because Node.js was unavailable."

    logical_node_count = len(
        fixture.get("writes", fixture.get("downloads", []))
    )

    return ExperimentResult(
        schema_version="0.4",
        run_id=run_id,
        case_id=fixture["case_id"],
        repetition=repetition,
        mode=mode,
        graph_id=f"rollback-fixture-{run_id}",
        task_id=task_id,
        started_at=started_at,
        finished_at=finished_at,
        git_commit=git_commit,
        python_version=python_version,
        node_version=node_version,
        os=operating_system,
        environment_fingerprint=environment_fingerprint,
        runner_command=runner_command,
        fixture_id=fixture["fixture_id"],
        objective_class=fixture["objective_class"],
        node_count=logical_node_count,
        dependency_edge_count=0,
        max_parallelism=1,
        tool_sequence=facts.tool_sequence,
        elapsed_ms=elapsed_ms,
        graph_elapsed_ms=0,
        critical_path_ms=0,
        parallel_saved_ms=0,
        approval_wait_ms=0,
        rollback_elapsed_ms=facts.rollback_elapsed_ms,
        status=status,
        expected_status=ground_truth["expected_status"],
        safety_outcome=safety_outcome,
        tool_executed_count=facts.tool_executed_count,
        unsafe_tool_executed_count=0,
        blocked_count=0,
        false_block_count=0,
        risk_escalation_count=0,
        check_count=facts.check_count,
        audit_event_count=0,
        approval_requested_count=0,
        approval_decision_count=0,
        manual_action_count=0,
        checkpoint_count=facts.checkpoint_count,
        pending_effect_count=facts.pending_effect_count,
        commit_count=facts.commit_count,
        rollback_count=facts.rollback_count,
        selective_rollback_count=facts.selective_rollback_count,
        residual_effect_count=facts.residual_effect_count,
        metrics={
            "affected_node_count": facts.affected_node_count,
            "rolled_back_effect_count": facts.rolled_back_effect_count,
            "preserved_node_count": facts.preserved_node_count,
            "preserved_effect_count": facts.preserved_effect_count,
        },
        audit_digest=None,
        raw_result_path=_relative_to_repo(raw_jsonl_path),
        error_code=error_code,
        notes=notes,
    )


def _write_jsonl(path: Path, result: ExperimentResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.model_dump_json(exclude_none=False)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _csv_header() -> list[str]:
    return list(ExperimentResult.model_fields)


def _csv_row(result: ExperimentResult) -> dict[str, object]:
    payload = result.model_dump(mode="json", exclude_none=False)
    for name, value in tuple(payload.items()):
        if isinstance(value, (list, dict)):
            payload[name] = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=isinstance(value, dict),
                separators=(",", ":"),
            )
        elif value is None:
            payload[name] = ""
    return payload


def _write_csv(path: Path, result: ExperimentResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = _csv_header()
    if path.exists() and path.stat().st_size > 0:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            existing_header = next(reader, [])
        if existing_header != header:
            raise RuntimeError(
                "existing rollback benchmark CSV header does not match ExperimentResult"
            )

    new_file = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="raise")
        if new_file:
            writer.writeheader()
        writer.writerow(_csv_row(result))
        handle.flush()
        os.fsync(handle.fileno())


async def _run_one(
    *,
    fixture: dict[str, Any],
    mode: ExperimentMode,
    repetition: int,
    workspace_root: Path,
    raw_jsonl_path: Path,
    raw_csv_path: Path,
    keep_workspace: bool,
    environment: dict[str, str],
) -> ExperimentResult:
    if mode not in SUPPORTED_MODES:
        raise ValueError(
            "phase A/B runner only supports ADAPTIVE_RUNTIME; "
            "BASELINE and FULL_GUARD require the frozen mode semantics"
        )
    if mode.value not in fixture["supported_modes"]:
        raise ValueError(
            f"fixture {fixture['case_id']} does not support mode {mode.value}"
        )

    run_id = f"{fixture['case_id'].lower()}-{uuid4().hex}"
    task_id = f"task-{run_id}"
    workspace_root.mkdir(parents=True, exist_ok=True)
    run_root = Path(
        tempfile.mkdtemp(
            prefix=f"{run_id}-",
            dir=workspace_root,
        )
    )
    facts = RunFacts()
    started_at = _utc_now()
    started_ns = perf_counter_ns()
    observation: ScenarioObservation | None = None
    error: Exception | None = None

    try:
        observation = await _run_scenario(
            fixture=fixture,
            run_id=run_id,
            task_id=task_id,
            run_root=run_root,
            facts=facts,
        )
    except Exception as caught:
        error = caught

    finished_at = _utc_now()
    elapsed_ms = _elapsed_ms(started_ns)
    result = _build_result(
        fixture=fixture,
        run_id=run_id,
        task_id=task_id,
        mode=mode,
        repetition=repetition,
        started_at=started_at,
        finished_at=finished_at,
        elapsed_ms=elapsed_ms,
        facts=facts,
        observation=observation,
        error=error,
        raw_jsonl_path=raw_jsonl_path,
        git_commit=environment["git_commit"],
        python_version=environment["python_version"],
        node_version=environment["node_version"],
        operating_system=environment["os"],
        environment_fingerprint=environment["environment_fingerprint"],
        runner_command=environment["runner_command"],
    )

    _write_jsonl(raw_jsonl_path, result)
    _write_csv(raw_csv_path, result)

    print(
        json.dumps(
            {
                "case_id": result.case_id,
                "git_commit": result.git_commit,
                "mode": result.mode.value,
                "raw_result_path": result.raw_result_path,
                "run_id": result.run_id,
                "status": result.status,
                "workspace": run_root.as_posix() if keep_workspace else "cleaned",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )

    if not keep_workspace:
        shutil.rmtree(run_root, ignore_errors=True)

    if error is not None:
        raise RuntimeError(
            f"{fixture['case_id']} produced a raw failure record: {error}"
        ) from error
    return result


async def run_benchmark(
    *,
    case_ids: list[str],
    mode: ExperimentMode,
    repetitions: int,
    output_dir: Path,
    workspace_root: Path,
    keep_workspaces: bool,
    reset_output: bool,
) -> list[ExperimentResult]:
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    if mode not in SUPPORTED_MODES:
        raise ValueError(
            "phase A/B runner only supports ADAPTIVE_RUNTIME; "
            "do not relabel the same execution path as another mode"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    raw_jsonl_path = output_dir / RAW_JSONL_NAME
    raw_csv_path = output_dir / RAW_CSV_NAME
    if reset_output:
        raw_jsonl_path.unlink(missing_ok=True)
        raw_csv_path.unlink(missing_ok=True)

    git_commit = _git_commit()
    python_version = platform.python_version()
    node_version = _node_version()
    operating_system = platform.platform()
    environment = {
        "git_commit": git_commit,
        "python_version": python_version,
        "node_version": node_version,
        "os": operating_system,
        "environment_fingerprint": _environment_fingerprint(
            git_commit=git_commit,
            python_version=python_version,
            node_version=node_version,
            operating_system=operating_system,
        ),
        "runner_command": _canonical_runner_command(),
    }

    results: list[ExperimentResult] = []
    for case_id in case_ids:
        fixture = _load_fixture(case_id)
        for repetition in range(1, repetitions + 1):
            result = await _run_one(
                fixture=fixture,
                mode=mode,
                repetition=repetition,
                workspace_root=workspace_root,
                raw_jsonl_path=raw_jsonl_path,
                raw_csv_path=raw_csv_path,
                keep_workspace=keep_workspaces,
                environment=environment,
            )
            results.append(result)
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the formal member 3 V2 selective rollback benchmark "
            "for File, Memory and Download fixtures."
        )
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--case-id",
        action="append",
        choices=sorted(FIXTURE_FILES),
        help="Run one case; repeat the option to run multiple cases.",
    )
    selection.add_argument(
        "--all-cases",
        action="store_true",
        help="Run M3-C01, M3-C02 and M3-C07.",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=[mode.value for mode in ExperimentMode],
    )
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "experiments" / "v2" / "results" / "raw",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=REPO_ROOT / ".runtime" / "benchmarks" / "v2-rollback",
    )
    parser.add_argument(
        "--keep-workspaces",
        action="store_true",
        help="Retain per-run workspace evidence under --workspace-root.",
    )
    parser.add_argument(
        "--reset-output",
        action="store_true",
        help="Delete the member 3 JSONL/CSV before this run.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    case_ids = sorted(FIXTURE_FILES) if args.all_cases else args.case_id
    try:
        results = asyncio.run(
            run_benchmark(
                case_ids=case_ids,
                mode=ExperimentMode(args.mode),
                repetitions=args.repetitions,
                output_dir=args.output_dir,
                workspace_root=args.workspace_root,
                keep_workspaces=args.keep_workspaces,
                reset_output=args.reset_output,
            )
        )
    except Exception as error:
        print(f"rollback benchmark failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    print(
        f"completed {len(results)} run(s); "
        f"jsonl={args.output_dir / RAW_JSONL_NAME}; "
        f"csv={args.output_dir / RAW_CSV_NAME}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
