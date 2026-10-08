"""Provider bridge tests using explicit substitutes for the absent P1 models.

FakeRuntimeContext and FakeDecisionResult are test-only stand-ins. They do not
define or validate the application's frozen public schema.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import threading
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import pytest

from ra_agent.intent.detection._types import (
    Assessment,
    CandidateAction,
    ContractSnapshot,
    DetectionInput,
    Disposition,
    ObservedBehavior,
)
from ra_agent.intent.detection.engine import IntentDetectorCore
from ra_agent.intent.detection.policy import DetectorPolicy
from ra_agent.intent.detection.provider import DetectorBusyError, IntentDetector

NOW = datetime(2026, 10, 7, 12, tzinfo=UTC)
OUTPUT_FIELDS = {
    "decision_id",
    "decision",
    "risk_score",
    "trigger_dimensions",
    "evidence_refs",
    "reason_code",
    "policy_version",
    "detector_version",
    "expires_at",
}


@dataclass(frozen=True)
class FakeRuntimeContext:
    """Test substitute whose adapter exposes a private detector snapshot."""

    snapshot: DetectionInput


@dataclass(frozen=True)
class FakeDecisionResult:
    """Strict test substitute with exactly the nine requested output fields."""

    decision_id: str
    decision: str
    risk_score: float
    trigger_dimensions: list[str]
    evidence_refs: list[str]
    reason_code: str
    policy_version: str
    detector_version: str
    expires_at: datetime

    def __post_init__(self) -> None:
        if UUID(self.decision_id).version != 4:
            raise ValueError("test result requires a UUID4 identity")
        if self.decision not in {item.value for item in Disposition}:
            raise ValueError("unknown test decision")
        if not math.isfinite(self.risk_score) or not 0 <= self.risk_score <= 1:
            raise ValueError("invalid test risk score")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("test expiry must be timezone-aware")


def _snapshot(task: str = "task-A") -> DetectionInput:
    return DetectionInput(
        contract=ContractSnapshot(
            task_id=task,
            contract_id=f"contract-{task}",
            version="contract-v1",
            goal="分析设备配置并生成风险报告",
            evidence_ref=f"user:{task}",
            goal_targets=(f"device-{task}",),
        ),
        candidate=CandidateAction(
            request_id=f"request-{task}",
            step_index=4,
            tool="read_config",
            action="read",
            target=f"device-{task}",
            subgoal="读取设备配置",
            evidence_ref=f"candidate:{task}",
        ),
        now=NOW,
    )


def _assessment(decision: Disposition = Disposition.CONTINUE) -> Assessment:
    return Assessment(
        disposition=decision,
        risk_score=0.4,
        trigger_dimensions=("fixture_dimension",),
        evidence_refs=("fixture:contract", "fixture:candidate"),
        reason_code="FIXTURE_REASON",
        reasons=("A structured test explanation, retained outside the frozen result.",),
        policy_version="fixture-policy-v1",
        detector_version="fixture-detector-v1",
        expires_at=NOW + timedelta(seconds=60),
        features=(("fixture_feature", 0.4),),
    )


class FixedAssessmentCore(IntentDetectorCore):
    """Test-only deterministic core for verifying the provider's transformation."""

    def __init__(self, assessment: Assessment, *, cache_size: int = 128) -> None:
        super().__init__(
            policy=DetectorPolicy("fixture-policy-v1", cache_size=cache_size)
        )
        self.assessment = assessment
        self.calls = 0

    def analyze(self, context: DetectionInput, /) -> Assessment:
        context.validate()
        self.calls += 1
        return self.assessment


class ControlledCPUCore(FixedAssessmentCore):
    """A finite CPU-bound fixture whose completion the test controls."""

    def __init__(self) -> None:
        super().__init__(_assessment())
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.worker_ident: int | None = None

    def analyze(self, context: DetectionInput, /) -> Assessment:
        self.worker_ident = threading.get_ident()
        self.started.set()
        deadline = time.monotonic() + 2.0
        value = 1
        try:
            while not self.release.is_set() and time.monotonic() < deadline:
                value = (value * 17 + 3) % 104729
            return super().analyze(context)
        finally:
            self.finished.set()


def _provider(
    core: IntentDetectorCore,
    *,
    max_pending_evaluations: int = 8,
) -> IntentDetector[FakeRuntimeContext, FakeDecisionResult]:
    return IntentDetector[FakeRuntimeContext, FakeDecisionResult](
        context_adapter=lambda context: context.snapshot,
        result_factory=FakeDecisionResult,
        core=core,
        max_pending_evaluations=max_pending_evaluations,
    )


@pytest.mark.parametrize("decision", list(Disposition))
async def test_all_five_dispositions_map_to_exactly_nine_result_fields(
    decision: Disposition,
):
    assessment = _assessment(decision)
    provider = _provider(FixedAssessmentCore(assessment))
    result = await provider.evaluate(FakeRuntimeContext(_snapshot()))
    assert isinstance(result, FakeDecisionResult)
    assert set(asdict(result)) == OUTPUT_FIELDS
    assert result.decision == decision.value
    assert UUID(result.decision_id).version == 4
    assert result.risk_score == assessment.risk_score
    assert result.trigger_dimensions == list(assessment.trigger_dimensions)
    assert result.evidence_refs == list(assessment.evidence_refs)
    assert result.reason_code == assessment.reason_code
    assert result.policy_version == assessment.policy_version
    assert result.detector_version == assessment.detector_version
    assert result.expires_at == assessment.expires_at
    assert result.expires_at.utcoffset() == timedelta(0)
    assert provider.explain(result.decision_id) is assessment
    assert provider.explain("unknown-decision") is None


async def test_evaluate_is_async_and_context_is_positional_only():
    provider = _provider(FixedAssessmentCore(_assessment()))
    assert inspect.iscoroutinefunction(provider.evaluate)
    assert (
        inspect.signature(provider.evaluate).parameters["context"].kind
        is inspect.Parameter.POSITIONAL_ONLY
    )
    with pytest.raises(TypeError, match="positional-only"):
        await cast(Any, provider.evaluate)(context=FakeRuntimeContext(_snapshot()))


async def test_result_constructor_receives_no_extra_public_fields():
    seen: list[dict[str, Any]] = []

    def factory(**fields: Any) -> FakeDecisionResult:
        seen.append(fields)
        if set(fields) != OUTPUT_FIELDS:
            raise ValueError("unexpected public output fields")
        return FakeDecisionResult(**fields)

    provider = IntentDetector[FakeRuntimeContext, FakeDecisionResult](
        context_adapter=lambda context: context.snapshot,
        result_factory=factory,
        core=FixedAssessmentCore(_assessment()),
    )
    first = await provider.evaluate(FakeRuntimeContext(_snapshot()))
    second = await provider.evaluate(FakeRuntimeContext(_snapshot()))
    assert first is not None and second is not None
    assert first.decision_id != second.decision_id
    assert len(seen) == 2


async def test_factory_validation_error_propagates_without_continue_or_explanation():
    decision_ids: list[str] = []

    def invalid_result_factory(**fields: Any) -> FakeDecisionResult:
        decision_ids.append(fields["decision_id"])
        raise ValueError("fixture public schema rejected this result")

    core = FixedAssessmentCore(_assessment(Disposition.BLOCK))
    provider = IntentDetector[FakeRuntimeContext, FakeDecisionResult](
        context_adapter=lambda context: context.snapshot,
        result_factory=invalid_result_factory,
        core=core,
    )
    with pytest.raises(ValueError, match="public schema rejected"):
        await provider.evaluate(FakeRuntimeContext(_snapshot()))
    assert core.calls == 1
    assert len(decision_ids) == 1
    assert provider.explain(decision_ids[0]) is None


async def test_adapter_exception_propagates_before_scoring_or_result_creation():
    core = FixedAssessmentCore(_assessment())
    factory_calls: list[dict[str, Any]] = []

    def bad_adapter(context: FakeRuntimeContext) -> DetectionInput:
        raise ValueError("fixture could not validate the trusted runtime context")

    def result_factory(**fields: Any) -> FakeDecisionResult:
        factory_calls.append(fields)
        return FakeDecisionResult(**fields)

    provider = IntentDetector[FakeRuntimeContext, FakeDecisionResult](
        context_adapter=bad_adapter,
        result_factory=result_factory,
        core=core,
    )
    with pytest.raises(ValueError, match="trusted runtime context"):
        await provider.evaluate(FakeRuntimeContext(_snapshot()))
    assert core.calls == 0
    assert factory_calls == []


async def test_cancelled_evaluation_never_constructs_a_result_or_explanation(
    monkeypatch,
):
    fixed_id = "659c90a2-d319-4df1-a1c5-fb18618e4604"
    monkeypatch.setattr(
        "ra_agent.intent.detection.provider.uuid4", lambda: UUID(fixed_id)
    )
    core = ControlledCPUCore()
    factory_calls: list[dict[str, Any]] = []

    def factory(**fields: Any) -> FakeDecisionResult:
        factory_calls.append(fields)
        return FakeDecisionResult(**fields)

    provider = IntentDetector[FakeRuntimeContext, FakeDecisionResult](
        context_adapter=lambda context: context.snapshot,
        result_factory=factory,
        core=core,
    )
    evaluation = asyncio.create_task(provider.evaluate(FakeRuntimeContext(_snapshot())))
    try:
        assert await asyncio.to_thread(core.started.wait, 1.0)
        evaluation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await evaluation
    finally:
        core.release.set()
        assert await asyncio.to_thread(core.finished.wait, 1.0)
    # Cancellation does not kill a worker thread. Its late completion must not
    # construct a runtime result or publish an explanation to the local cache.
    await asyncio.sleep(0)
    assert factory_calls == []
    assert provider.explain(fixed_id) is None


async def test_cpu_scoring_runs_off_the_event_loop_and_allows_a_heartbeat():
    core = ControlledCPUCore()
    provider = _provider(core)
    loop_thread = threading.get_ident()
    evaluation = asyncio.create_task(provider.evaluate(FakeRuntimeContext(_snapshot())))
    heartbeat = asyncio.Event()
    try:
        assert await asyncio.to_thread(core.started.wait, 1.0)
        asyncio.get_running_loop().call_soon(heartbeat.set)
        await asyncio.wait_for(heartbeat.wait(), timeout=0.5)
        assert not core.finished.is_set()
        assert core.worker_ident != loop_thread
    finally:
        core.release.set()
        result = await evaluation
    assert result is not None


async def test_concurrent_evaluations_keep_task_histories_and_evidence_separate():
    core = IntentDetectorCore(
        policy=DetectorPolicy(
            "concurrency-fixture", enable_semantic=False, cache_size=32
        )
    )
    provider = _provider(core, max_pending_evaluations=16)
    contexts: list[FakeRuntimeContext] = []
    for index in range(12):
        task = f"isolated-{index}"
        snapshot = _snapshot(task)
        risky = index % 2 == 0
        history = tuple(
            ObservedBehavior(
                event_id=f"event-{step}",  # Same IDs in different tasks must not contaminate state.
                task_id=task,
                contract_version=snapshot.contract.version,
                step_index=step,
                tool="read_config",
                action="read",
                target=f"unrelated-{step}" if risky else snapshot.candidate.target,
                evidence_ref=f"history:{task}:{step}",
                verified_progress=False if risky else True,
            )
            for step in range(3)
        )
        contexts.append(FakeRuntimeContext(replace(snapshot, history=history)))
    results = await asyncio.gather(
        *(provider.evaluate(context) for context in contexts)
    )
    for index, result in enumerate(results):
        assert result is not None
        assert result.decision == ("REPLAN" if index % 2 == 0 else "CONTINUE")
        task = f"isolated-{index}"
        assert f"user:{task}" in result.evidence_refs
        assert all(
            not ref.startswith("history:") or ref.startswith(f"history:{task}:")
            for ref in result.evidence_refs
        )
    assert core.state_sizes["tasks"] == 12
    assert core.state_sizes["events"] == 36


@pytest.mark.parametrize("cache_size", [0, 1, 3])
async def test_explanation_retention_obeys_bounded_cache_including_zero(
    cache_size: int,
):
    provider = _provider(FixedAssessmentCore(_assessment(), cache_size=cache_size))
    results = [
        await provider.evaluate(FakeRuntimeContext(_snapshot())) for _ in range(7)
    ]
    for index, result in enumerate(results):
        assert result is not None
        retained = cache_size > 0 and index >= len(results) - cache_size
        assert (provider.explain(result.decision_id) is not None) is retained


async def test_busy_admission_rejects_before_adapting_another_snapshot():
    core = ControlledCPUCore()
    adapted: list[str] = []

    def adapter(context: FakeRuntimeContext) -> DetectionInput:
        adapted.append(context.snapshot.contract.task_id)
        return context.snapshot

    provider = IntentDetector[FakeRuntimeContext, FakeDecisionResult](
        context_adapter=adapter,
        result_factory=FakeDecisionResult,
        core=core,
        max_pending_evaluations=1,
    )
    first = asyncio.create_task(
        provider.evaluate(FakeRuntimeContext(_snapshot("admitted")))
    )
    try:
        assert await asyncio.to_thread(core.started.wait, 1.0)
        with pytest.raises(DetectorBusyError):
            await provider.evaluate(FakeRuntimeContext(_snapshot("busy")))
        assert adapted == ["admitted"]
    finally:
        core.release.set()
        assert await first is not None
    assert await provider.evaluate(FakeRuntimeContext(_snapshot("next"))) is not None
    assert adapted == ["admitted", "next"]


async def test_cancellation_keeps_admission_reserved_until_worker_really_finishes():
    core = ControlledCPUCore()
    provider = _provider(core, max_pending_evaluations=1)
    first = asyncio.create_task(provider.evaluate(FakeRuntimeContext(_snapshot())))
    try:
        assert await asyncio.to_thread(core.started.wait, 1.0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not core.finished.is_set()
        with pytest.raises(DetectorBusyError):
            await provider.evaluate(FakeRuntimeContext(_snapshot("still-busy")))
    finally:
        core.release.set()
        assert await asyncio.to_thread(core.finished.wait, 1.0)
    # A real worker completion callback releases admission; cancelling its caller does not.
    await asyncio.sleep(0.01)
    assert (
        await provider.evaluate(FakeRuntimeContext(_snapshot("after-finish")))
        is not None
    )


async def test_adapter_failure_releases_admission_for_a_valid_next_call():
    count = 0

    def adapter(context: FakeRuntimeContext) -> DetectionInput:
        nonlocal count
        count += 1
        if count == 1:
            raise ValueError("fixture adapter failure")
        return context.snapshot

    provider = IntentDetector[FakeRuntimeContext, FakeDecisionResult](
        context_adapter=adapter,
        result_factory=FakeDecisionResult,
        core=FixedAssessmentCore(_assessment()),
        max_pending_evaluations=1,
    )
    with pytest.raises(ValueError, match="adapter failure"):
        await provider.evaluate(FakeRuntimeContext(_snapshot()))
    assert await provider.evaluate(FakeRuntimeContext(_snapshot())) is not None


async def test_factory_failure_releases_admission_for_a_valid_next_call():
    count = 0

    def factory(**fields: Any) -> FakeDecisionResult:
        nonlocal count
        count += 1
        if count == 1:
            raise ValueError("fixture factory failure")
        return FakeDecisionResult(**fields)

    provider = IntentDetector[FakeRuntimeContext, FakeDecisionResult](
        context_adapter=lambda context: context.snapshot,
        result_factory=factory,
        core=FixedAssessmentCore(_assessment()),
        max_pending_evaluations=1,
    )
    with pytest.raises(ValueError, match="factory failure"):
        await provider.evaluate(FakeRuntimeContext(_snapshot()))
    assert await provider.evaluate(FakeRuntimeContext(_snapshot())) is not None


async def test_invalid_snapshot_releases_admission_without_starting_a_worker():
    core = FixedAssessmentCore(_assessment())
    provider = _provider(core, max_pending_evaluations=1)
    valid = _snapshot()
    invalid = replace(valid, contract=replace(valid.contract, goal=""))
    with pytest.raises(ValueError, match="missing required"):
        await provider.evaluate(FakeRuntimeContext(invalid))
    assert core.calls == 0
    assert await provider.evaluate(FakeRuntimeContext(valid)) is not None


async def test_none_result_is_rejected_and_does_not_leave_an_explanation():
    ids: list[str] = []

    def factory(**fields: Any) -> FakeDecisionResult:
        ids.append(fields["decision_id"])
        return cast(Any, None)

    provider = IntentDetector[FakeRuntimeContext, FakeDecisionResult](
        context_adapter=lambda context: context.snapshot,
        result_factory=factory,
        core=FixedAssessmentCore(_assessment()),
        max_pending_evaluations=1,
    )
    with pytest.raises(ValueError, match="returned None"):
        await provider.evaluate(FakeRuntimeContext(_snapshot()))
    assert provider.pending_evaluations == 0
    assert len(ids) == 1
    assert provider.explain(ids[0]) is None


async def test_worker_exception_releases_admission_for_next_call():
    class RaisingOnceCore(FixedAssessmentCore):
        def analyze(self, context: DetectionInput, /) -> Assessment:
            if self.calls == 0:
                self.calls += 1
                raise RuntimeError("fixture worker failure")
            return super().analyze(context)

    provider = _provider(RaisingOnceCore(_assessment()), max_pending_evaluations=1)
    with pytest.raises(RuntimeError, match="worker failure"):
        await provider.evaluate(FakeRuntimeContext(_snapshot()))
    assert await provider.evaluate(FakeRuntimeContext(_snapshot())) is not None


async def test_submission_failure_releases_admission_for_next_call(monkeypatch):
    loop = asyncio.get_running_loop()
    submit = loop.run_in_executor
    count = 0

    def fail_once(executor: Any, function: Any, *args: Any):
        nonlocal count
        count += 1
        if count == 1:
            raise RuntimeError("fixture executor submission failure")
        return submit(executor, function, *args)

    monkeypatch.setattr(loop, "run_in_executor", fail_once)
    provider = _provider(FixedAssessmentCore(_assessment()), max_pending_evaluations=1)
    with pytest.raises(RuntimeError, match="submission failure"):
        await provider.evaluate(FakeRuntimeContext(_snapshot()))
    assert await provider.evaluate(FakeRuntimeContext(_snapshot())) is not None


@pytest.mark.parametrize("invalid_limit", [0, -1, True, "2"])
def test_admission_limit_must_be_a_positive_integer(invalid_limit: Any):
    with pytest.raises(ValueError):
        _provider(
            FixedAssessmentCore(_assessment()), max_pending_evaluations=invalid_limit
        )
