"""Reproducible V2 member-2 security experiment.

The runner executes the deterministic Risk/Policy/Permission/Approval pipeline.
Its probe executor has no external side effect; counters indicate whether a
request reached that controlled execution boundary.
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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter_ns

from ra_agent.contracts import (
    ApprovalRequest,
    ApprovalStatus,
    ExperimentMode,
    ExperimentResult,
    PermissionCheckResult,
    PolicyDecision,
    RiskLevel,
    RiskVerdict,
    SourceType,
    ToolCallRequest,
    ToolSpec,
)
from ra_agent.runtime.idempotency import request_fingerprint
from ra_agent.security.adaptive_approval import AdaptiveApprovalEvaluator
from ra_agent.security.approval_service import MockApprovalService
from ra_agent.security.permission_gate import RuleBasedPermissionGate
from ra_agent.security.policy_engine import RuleBasedPolicyEngine
from ra_agent.security.risk_classifier import RuleBasedRiskClassifier
from ra_agent.security.rule_engine import RuleEngine
from ra_agent.tools import DEFAULT_TOOL_SPECS

ROOT = Path(__file__).resolve().parents[3]
CONFIG_ROOT = ROOT / "configs"
DEFAULT_FIXTURE_PATH = (
    ROOT / "experiments" / "v2" / "fixtures" / "security_safety_v1.json"
)

_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
    RiskLevel.FORBIDDEN: 4,
}


@dataclass(frozen=True, slots=True)
class SecurityCase:
    case_id: str
    family: str
    tool_name: str
    arguments: dict[str, object]
    source_type: SourceType
    expected_status: str
    approval_action: str = "grant"
    ground_truth: str = "UNSAFE"

    @property
    def objective_class(self) -> str:
        return self.ground_truth


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    status: str
    verdict: RiskVerdict
    decision: PolicyDecision
    permission: PermissionCheckResult | None
    tool_executed_count: int
    unsafe_tool_executed_count: int
    blocked_count: int
    risk_escalation_count: int
    check_count: int
    approval_requested_count: int
    approval_decision_count: int
    manual_action_count: int
    approval_wait_ms: int
    evidence: dict[str, object]


class FloorOnlyApprovalEvaluator(AdaptiveApprovalEvaluator):
    """Controlled BASELINE ablation documented in every raw result row."""

    def risk_signals(
        self,
        request: ToolCallRequest,
        tool_spec: ToolSpec,
    ) -> tuple[str, ...]:
        return ()


class ProbeExecutor:
    """Count a reached execution boundary without touching OS or workspace state."""

    async def execute(self, request: ToolCallRequest) -> dict[str, str]:
        await asyncio.sleep(0)
        return {"request_id": request.request_id, "tool_name": request.tool_name}


def security_cases(
    fixture_path: Path = DEFAULT_FIXTURE_PATH,
) -> tuple[SecurityCase, ...]:
    """Load and fail-closed validate the frozen safety ground truth."""

    try:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid security fixture: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("security fixture schema_version must be 1")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("security fixture cases must be a non-empty list")

    cases: list[SecurityCase] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise ValueError(f"security fixture case {index} must be an object")
        case_id = _required_string(raw_case, "case_id", index)
        if case_id in seen_ids:
            raise ValueError(f"duplicate security fixture case_id: {case_id}")
        seen_ids.add(case_id)
        expected_status = _required_string(raw_case, "expected_status", index)
        ground_truth = _required_string(raw_case, "ground_truth", index)
        approval_action = _required_string(raw_case, "approval_action", index)
        if expected_status not in {"SUCCESS", "BLOCKED"}:
            raise ValueError(f"unsupported expected_status for {case_id}")
        if ground_truth not in {"SAFE", "UNSAFE"}:
            raise ValueError(f"unsupported ground_truth for {case_id}")
        if (expected_status == "SUCCESS") != (ground_truth == "SAFE"):
            raise ValueError(f"ground truth and expected status disagree for {case_id}")
        if approval_action not in {"grant", "deny", "expire"}:
            raise ValueError(f"unsupported approval_action for {case_id}")
        arguments = raw_case.get("arguments")
        if not isinstance(arguments, dict):
            raise ValueError(
                f"security fixture arguments must be an object for {case_id}"
            )
        try:
            source_type = SourceType(_required_string(raw_case, "source_type", index))
        except ValueError as error:
            raise ValueError(f"unsupported source_type for {case_id}") from error
        cases.append(
            SecurityCase(
                case_id=case_id,
                family=_required_string(raw_case, "family", index),
                tool_name=_required_string(raw_case, "tool_name", index),
                arguments=arguments,
                source_type=source_type,
                expected_status=expected_status,
                approval_action=approval_action,
                ground_truth=ground_truth,
            )
        )
    return tuple(cases)


def _required_string(payload: dict[str, object], key: str, index: int) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"security fixture case {index} has invalid {key}")
    return value.strip()


async def run_experiments(
    *,
    output_directory: Path,
    output_stem: str,
    repetitions: int,
    runner_command: str,
) -> tuple[Path, Path, list[ExperimentResult]]:
    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    output_directory.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_directory / f"{output_stem}.jsonl"
    csv_path = output_directory / f"{output_stem}.csv"
    try:
        relative_jsonl = jsonl_path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        relative_jsonl = jsonl_path.resolve().as_posix()

    rules = RuleEngine.from_directory(CONFIG_ROOT, strict=True)
    tool_specs = {spec.name: spec for spec in DEFAULT_TOOL_SPECS}
    git_commit = _command_output(
        [
            "git",
            "-c",
            f"safe.directory={ROOT.resolve().as_posix()}",
            "rev-parse",
            "HEAD",
        ],
        cwd=ROOT,
    )
    node_version = _command_output(["node", "--version"], cwd=ROOT, fallback="N/A")
    python_version = platform.python_version()
    os_name = platform.platform()
    environment_fingerprint = _environment_fingerprint(
        git_commit=git_commit,
        python_version=python_version,
        node_version=node_version,
        os_name=os_name,
    )
    run_id = f"security-v2-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    results: list[ExperimentResult] = []

    for mode in ExperimentMode:
        for case in security_cases():
            for repetition in range(repetitions):
                result = await _run_once(
                    mode=mode,
                    case=case,
                    repetition=repetition,
                    run_id=run_id,
                    rules=rules,
                    tool_specs=tool_specs,
                    git_commit=git_commit,
                    python_version=python_version,
                    node_version=node_version,
                    os_name=os_name,
                    environment_fingerprint=environment_fingerprint,
                    runner_command=runner_command,
                    raw_result_path=relative_jsonl,
                )
                results.append(result)

    with jsonl_path.open("w", encoding="utf-8", newline="\n") as handle:
        for result in results:
            handle.write(
                json.dumps(
                    result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
                )
                + "\n"
            )
    fieldnames = list(ExperimentResult.model_fields)
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            row = result.model_dump(mode="json")
            row["tool_sequence"] = json.dumps(row["tool_sequence"], ensure_ascii=False)
            row["metrics"] = json.dumps(row["metrics"], sort_keys=True)
            writer.writerow(row)
    return jsonl_path, csv_path, results


async def _run_once(
    *,
    mode: ExperimentMode,
    case: SecurityCase,
    repetition: int,
    run_id: str,
    rules: RuleEngine,
    tool_specs: dict[str, ToolSpec],
    git_commit: str,
    python_version: str,
    node_version: str,
    os_name: str,
    environment_fingerprint: str,
    runner_command: str,
    raw_result_path: str,
) -> ExperimentResult:
    requested_at = datetime.now(UTC)
    started_ns = perf_counter_ns()
    request_id = f"{mode.value.lower()}-{case.case_id}-{repetition}"
    request = ToolCallRequest(
        task_id=f"task-{request_id}",
        step_id=f"step-{request_id}",
        request_id=request_id,
        tool_name=case.tool_name,
        arguments=case.arguments,
        objective=f"V2 security experiment fixture {case.case_id}",
        context_summary="deterministic member-2 security pipeline experiment",
        source_type=case.source_type,
        requested_at=requested_at,
    )
    outcome = await _evaluate_pipeline(
        mode=mode,
        case=case,
        request=request,
        rules=rules,
        tool_specs=tool_specs,
    )
    finished_at = datetime.now(UTC)
    elapsed_ms = max(0, (perf_counter_ns() - started_ns) // 1_000_000)
    safety_outcome = _safety_outcome(case, outcome)
    notes = _mode_notes(mode, case)
    return ExperimentResult(
        schema_version="0.4",
        run_id=run_id,
        case_id=case.case_id,
        repetition=repetition,
        mode=mode,
        graph_id=f"graph-{request_id}",
        task_id=request.task_id,
        started_at=requested_at,
        finished_at=finished_at,
        git_commit=git_commit,
        python_version=python_version,
        node_version=node_version,
        os=os_name,
        environment_fingerprint=environment_fingerprint,
        runner_command=runner_command,
        fixture_id=case.case_id,
        objective_class=case.objective_class,
        node_count=1,
        dependency_edge_count=0,
        max_parallelism=1,
        tool_sequence=[case.tool_name],
        elapsed_ms=elapsed_ms,
        graph_elapsed_ms=0,
        critical_path_ms=0,
        parallel_saved_ms=0,
        approval_wait_ms=outcome.approval_wait_ms,
        rollback_elapsed_ms=0,
        status=outcome.status,
        expected_status=case.expected_status,
        safety_outcome=safety_outcome,
        tool_executed_count=outcome.tool_executed_count,
        unsafe_tool_executed_count=outcome.unsafe_tool_executed_count,
        blocked_count=outcome.blocked_count,
        false_block_count=int(safety_outcome == "FALSE_BLOCK"),
        risk_escalation_count=outcome.risk_escalation_count,
        check_count=outcome.check_count,
        audit_event_count=0,
        approval_requested_count=outcome.approval_requested_count,
        approval_decision_count=outcome.approval_decision_count,
        manual_action_count=outcome.manual_action_count,
        checkpoint_count=0,
        pending_effect_count=0,
        commit_count=0,
        rollback_count=0,
        selective_rollback_count=0,
        residual_effect_count=0,
        metrics={
            "sum_node_elapsed_ms": 0,
            "max_observed_concurrency": 0,
            "nodes_completed_during_approval": 0,
            "hidden_approval_wait_ms": 0,
            "affected_node_count": 0,
            "rolled_back_effect_count": 0,
            "preserved_node_count": 0,
            "preserved_effect_count": 0,
        },
        audit_digest=None,
        raw_result_path=raw_result_path,
        error_code=None if outcome.status == "SUCCESS" else outcome.status,
        notes=notes,
    )


async def _evaluate_pipeline(
    *,
    mode: ExperimentMode,
    case: SecurityCase,
    request: ToolCallRequest,
    rules: RuleEngine,
    tool_specs: dict[str, ToolSpec],
) -> PipelineOutcome:
    spec = tool_specs[request.tool_name]
    adaptive = AdaptiveApprovalEvaluator(rules)
    baseline = FloorOnlyApprovalEvaluator(rules)
    evaluator = baseline if mode is ExperimentMode.BASELINE else adaptive
    permission_gate = RuleBasedPermissionGate(rules, approval_evaluator=evaluator)
    classifier = RuleBasedRiskClassifier(
        rules,
        tool_specs,
        approval_evaluator=evaluator,
    )
    check_count = 0
    if mode is ExperimentMode.BASELINE:
        floor = spec.base_risk
        verdict = RiskVerdict(
            request_id=request.request_id,
            risk_level=floor,
            recommended_decision=rules.decision_for(floor),
            reason=(
                f"BASELINE ablation; tool_floor={floor.value}; "
                "adaptive signals and lineage checks bypassed"
            ),
            matched_rules=[f"tool_spec.{request.tool_name}.base_risk"],
            requires_deep_check=floor is not RiskLevel.LOW,
            requires_checkpoint=spec.side_effect_type not in {"NONE", "READ"},
        )
        check_count += 1
    else:
        verdict = await classifier.classify(request)
        check_count += 1
    decision = await RuleBasedPolicyEngine(rules).decide(verdict)
    check_count += 1
    if mode is ExperimentMode.FULL_GUARD and decision is not PolicyDecision.BLOCK:
        decision = PolicyDecision.REQUEST_APPROVAL

    floor = spec.base_risk
    escalated = int(_RISK_ORDER[verdict.risk_level] > _RISK_ORDER[floor])
    evidence: dict[str, object] = {
        "mode": mode.value,
        "request_id": request.request_id,
        "risk": verdict.risk_level.value,
        "decision": decision.value,
        "signals": verdict.signals,
        "matched_rules": verdict.matched_rules,
    }
    if decision is PolicyDecision.BLOCK:
        return _blocked_outcome(
            verdict=verdict,
            decision=decision,
            risk_escalation_count=escalated,
            check_count=check_count,
            evidence=evidence,
        )

    permission: PermissionCheckResult | None = None
    approval_requested = 0
    approval_decided = 0
    manual_actions = 0
    approval_wait_ms = 0
    approval_granted = False
    if decision is PolicyDecision.REQUEST_APPROVAL:
        permission = await permission_gate.check(request, spec)
        check_count += 1
        approval_requested = 1
        approval = _build_approval(
            mode=mode,
            request=request,
            spec=spec,
            verdict=verdict,
            permission=permission,
            evaluator=evaluator,
        )
        service = MockApprovalService()
        await service.create(approval)
        wait_started = perf_counter_ns()
        if case.approval_action == "deny":
            approval_decision = await service.deny(
                approval.approval_id,
                "experiment-reviewer",
                "scripted denial for unsafe fixture",
            )
        elif case.approval_action == "expire":
            approval_decision = await service.expire(approval.approval_id)
        else:
            approval_decision = await service.grant(
                approval.approval_id,
                "experiment-reviewer",
                "scripted grant for safe fixture",
            )
        approval_wait_ms = max(0, (perf_counter_ns() - wait_started) // 1_000_000)
        approval_decided = 1
        manual_actions = int(case.approval_action in {"grant", "deny"})
        if (
            approval_decision.task_id != request.task_id
            or approval_decision.step_id != request.step_id
            or approval_decision.request_id != request.request_id
        ):
            raise ValueError("approval decision lost original request correlation")
        approval_granted = approval_decision.status is ApprovalStatus.GRANTED
        evidence["approval_status"] = approval_decision.status.value
        evidence["approval_reason"] = approval.reason
        if not approval_granted:
            return PipelineOutcome(
                status="BLOCKED",
                verdict=verdict,
                decision=decision,
                permission=permission,
                tool_executed_count=0,
                unsafe_tool_executed_count=0,
                blocked_count=1,
                risk_escalation_count=escalated,
                check_count=check_count,
                approval_requested_count=approval_requested,
                approval_decision_count=approval_decided,
                manual_action_count=manual_actions,
                approval_wait_ms=approval_wait_ms,
                evidence=evidence,
            )

    if permission is None:
        permission = await permission_gate.check(request, spec)
        check_count += 1
    evidence["permission_allowed"] = permission.allowed
    evidence["permission_requires_approval"] = permission.requires_approval
    if not permission.allowed or (
        permission.requires_approval and not approval_granted
    ):
        return PipelineOutcome(
            status="BLOCKED",
            verdict=verdict,
            decision=decision,
            permission=permission,
            tool_executed_count=0,
            unsafe_tool_executed_count=0,
            blocked_count=1,
            risk_escalation_count=escalated,
            check_count=check_count,
            approval_requested_count=approval_requested,
            approval_decision_count=approval_decided,
            manual_action_count=manual_actions,
            approval_wait_ms=approval_wait_ms,
            evidence=evidence,
        )

    probe_output = await ProbeExecutor().execute(request)
    unsafe_execution = int(case.expected_status == "BLOCKED")
    evidence["probe_output"] = probe_output
    return PipelineOutcome(
        status="SUCCESS",
        verdict=verdict,
        decision=decision,
        permission=permission,
        tool_executed_count=1,
        unsafe_tool_executed_count=unsafe_execution,
        blocked_count=0,
        risk_escalation_count=escalated,
        check_count=check_count,
        approval_requested_count=approval_requested,
        approval_decision_count=approval_decided,
        manual_action_count=manual_actions,
        approval_wait_ms=approval_wait_ms,
        evidence=evidence,
    )


def _build_approval(
    *,
    mode: ExperimentMode,
    request: ToolCallRequest,
    spec: ToolSpec,
    verdict: RiskVerdict,
    permission: PermissionCheckResult,
    evaluator: AdaptiveApprovalEvaluator,
) -> ApprovalRequest:
    now = datetime.now(UTC)
    approval_id = f"approval-{request.request_id}"
    fingerprint = request_fingerprint(request)
    if mode is ExperimentMode.FULL_GUARD and (
        verdict.risk_level is not RiskLevel.HIGH and not permission.requires_approval
    ):
        return ApprovalRequest(
            approval_id=approval_id,
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            tool_name=request.tool_name,
            request_fingerprint=fingerprint,
            reason="FULL_GUARD requires explicit approval for every non-blocked request",
            requested_at=now,
            expires_at=now + timedelta(minutes=15),
        )
    return evaluator.build_request(
        request,
        spec,
        verdict,
        permission,
        approval_id=approval_id,
        request_fingerprint=fingerprint,
        requested_at=now,
    )


def _blocked_outcome(
    *,
    verdict: RiskVerdict,
    decision: PolicyDecision,
    risk_escalation_count: int,
    check_count: int,
    evidence: dict[str, object],
) -> PipelineOutcome:
    return PipelineOutcome(
        status="BLOCKED",
        verdict=verdict,
        decision=decision,
        permission=None,
        tool_executed_count=0,
        unsafe_tool_executed_count=0,
        blocked_count=1,
        risk_escalation_count=risk_escalation_count,
        check_count=check_count,
        approval_requested_count=0,
        approval_decision_count=0,
        manual_action_count=0,
        approval_wait_ms=0,
        evidence=evidence,
    )


def _safety_outcome(case: SecurityCase, outcome: PipelineOutcome) -> str:
    if case.expected_status == "BLOCKED":
        return "UNSAFE_BLOCKED" if outcome.status == "BLOCKED" else "UNSAFE_ADMITTED"
    return "SAFE_ALLOWED" if outcome.status == "SUCCESS" else "FALSE_BLOCK"


def _mode_notes(mode: ExperimentMode, case: SecurityCase) -> str:
    common = (
        "Security-pipeline-only run with a no-side-effect probe executor; "
        f"family={case.family}; ground_truth={case.ground_truth}; "
        f"scripted_approval_action={case.approval_action}; "
        "TaskGraph/Audit/rollback metrics are not applicable and are 0; "
        "audit_digest is null; token_usage=N/A."
    )
    if mode is ExperimentMode.BASELINE:
        return (
            "BASELINE bypasses adaptive signals, prompt-injection, lineage, and egress "
            f"checks; it retains ToolSpec floors and static permissions. {common}"
        )
    if mode is ExperimentMode.FULL_GUARD:
        return f"FULL_GUARD requests approval for every non-blocked request. {common}"
    return (
        f"ADAPTIVE_RUNTIME uses the V2 member-2 implementation without bypass. {common}"
    )


def _environment_fingerprint(
    *,
    git_commit: str,
    python_version: str,
    node_version: str,
    os_name: str,
) -> str:
    config_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (
            CONFIG_ROOT / "risk_rules.yaml",
            CONFIG_ROOT / "permissions.yaml",
            CONFIG_ROOT / "sensitive_paths.yaml",
        )
    }
    payload = {
        "git_commit": git_commit,
        "python_version": python_version,
        "node_version": node_version,
        "os": os_name,
        "config_sha256": config_hashes,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _command_output(
    command: list[str],
    *,
    cwd: Path,
    fallback: str | None = None,
) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        if fallback is not None:
            return fallback
        raise
    return completed.stdout.strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--output-stem", required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runner_command = (
        "python -m experiments.v2.runners.run_safety_evaluation "
        f"--output-directory {args.output_directory.as_posix()} "
        f"--output-stem {args.output_stem} --repetitions {args.repetitions}"
    )
    jsonl_path, csv_path, results = asyncio.run(
        run_experiments(
            output_directory=args.output_directory,
            output_stem=args.output_stem,
            repetitions=args.repetitions,
            runner_command=runner_command,
        )
    )
    print(f"wrote {len(results)} rows to {jsonl_path} and {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
