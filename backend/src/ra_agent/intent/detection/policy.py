"""Bounded detector settings and offline, reviewed policy promotion.

This module never changes task authorizations or other hard runtime constraints.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, datetime
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class DetectorPolicy:
    """Immutable soft detector settings; limits bound per-process working state."""

    version: str
    window_size: int = 12
    max_tasks: int = 64
    cache_size: int = 128
    max_sources: int = 16
    max_text_chars: int = 4096
    max_evidence: int = 12
    decision_ttl_seconds: int = 60
    semantic_threshold: float = 0.75
    sequence_threshold: float = 0.6
    confirmation_threshold: float = 0.55
    max_no_progress: int = 6
    max_repeated_failures: int = 3
    max_replans: int = 3
    enable_semantic: bool = True
    enable_sequence: bool = True
    enable_sources: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip() or len(self.version) > 128:
            raise ValueError("version must be a non-empty string of at most 128 characters")
        limits = {
            "window_size": (1, 256),
            "max_tasks": (1, 1024),
            "cache_size": (0, 4096),
            "max_sources": (1, 128),
            "max_text_chars": (128, 65536),
            "max_evidence": (1, 128),
            "decision_ttl_seconds": (1, 3600),
            "max_no_progress": (1, 256),
            "max_repeated_failures": (1, 256),
            "max_replans": (0, 32),
        }
        for name, (minimum, maximum) in limits.items():
            value = getattr(self, name)
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
        for name in ("semantic_threshold", "sequence_threshold", "confirmation_threshold"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0.0 < value <= 1.0
            ):
                raise ValueError(f"{name} must be finite and in (0, 1]")
        for field in fields(self):
            if field.name.startswith("enable_") and type(getattr(self, field.name)) is not bool:
                raise ValueError(f"{field.name} must be a boolean")


_MAX_CASES = 10_000
_MAX_RECORDS = 64
_MAX_EVENTS = 1024
_THRESHOLD_FIELDS = frozenset(
    {"semantic_threshold", "sequence_threshold", "confirmation_threshold"}
)
_REVIEW_SOURCES = frozenset({"human_review", "reviewed_environment_evidence"})


def _text(value: str, name: str, *, limit: int = 256) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be non-empty text of at most {limit} characters")


def _fraction(value: float, name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError(f"{name} must be finite and in [0, 1]")


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ReviewedCase:
    """An offline human-reviewed score, never an online detector input.

    Review identity is supplied by the caller's trusted review workflow. A user
    clicking 'continue' and a tool/website claiming to be trusted are not labels.
    """

    case_id: str
    template_group: str
    split: Literal["validation", "test", "train"]
    label: bool
    risk_score: float
    reviewer: str
    source: str

    def __post_init__(self) -> None:
        for name in ("case_id", "template_group", "reviewer"):
            _text(getattr(self, name), name)
        if self.split not in {"validation", "test", "train"}:
            raise ValueError("unsupported score split")
        if type(self.label) is not bool:
            raise ValueError("label must be a reviewed boolean")
        _fraction(self.risk_score, "risk_score")
        if self.source not in _REVIEW_SOURCES:
            raise ValueError("label source must be an explicitly reviewed source")


@dataclass(frozen=True, slots=True)
class RegressionCase:
    """Independent observed hard-boundary result for a candidate policy."""

    case_id: str
    template_group: str
    candidate_version: str
    expected_block: bool
    blocked: bool
    reviewer: str
    source: str
    split: Literal["regression"] = "regression"

    def __post_init__(self) -> None:
        for name in ("case_id", "template_group", "candidate_version", "reviewer"):
            _text(getattr(self, name), name)
        if self.split != "regression":
            raise ValueError("hard-boundary observations require the regression split")
        if type(self.expected_block) is not bool or type(self.blocked) is not bool:
            raise ValueError("regression observations must be booleans")
        if self.source not in _REVIEW_SOURCES:
            raise ValueError("regression source must be explicitly reviewed")


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    """Reproducible evaluation with immutable input data and selection criteria."""

    evaluation_id: str
    base_policy: DetectorPolicy
    candidate_policy: DetectorPolicy
    threshold_field: str
    thresholds: tuple[float, ...]
    validation_cases: tuple[ReviewedCase, ...]
    regression_cases: tuple[RegressionCase, ...]
    excluded_template_groups: tuple[str, ...]
    max_false_positive_rate: float
    min_recall: float
    false_positive_rate: float
    recall: float
    regression_passed: bool
    accepted: bool
    data_digest: str

    def __post_init__(self) -> None:
        if type(self.accepted) is not bool or type(self.regression_passed) is not bool:
            raise ValueError("evaluation outcomes must be booleans")
        for name in ("max_false_positive_rate", "min_recall", "false_positive_rate", "recall"):
            _fraction(getattr(self, name), name)
        for name in (
            "thresholds",
            "validation_cases",
            "regression_cases",
            "excluded_template_groups",
        ):
            if not isinstance(getattr(self, name), tuple):
                raise ValueError(f"{name} must be immutable")


def calibrate_policy(
    base_policy: DetectorPolicy,
    *,
    candidate_version: str,
    validation_cases: tuple[ReviewedCase, ...],
    regression_cases: tuple[RegressionCase, ...],
    threshold_field: str = "semantic_threshold",
    thresholds: tuple[float, ...] = (0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9),
    max_false_positive_rate: float = 0.05,
    min_recall: float = 0.8,
    excluded_template_groups: tuple[str, ...] = (),
) -> PolicyEvaluation:
    """Choose one soft threshold using validation scores and independent regression.

    ``excluded_template_groups`` contains train/test template identifiers supplied
    by the dataset owner, without test labels/scores. This function neither runs
    an agent nor verifies the authenticity of externally submitted review data.
    Regression observations must be generated for this candidate version by the
    independent runner; caller-recorded results are not new runtime executions.
    """

    if not isinstance(base_policy, DetectorPolicy):
        raise ValueError("base_policy must be a DetectorPolicy")
    _text(candidate_version, "candidate_version", limit=128)
    if candidate_version == base_policy.version:
        raise ValueError("a candidate needs a new policy version")
    if threshold_field not in _THRESHOLD_FIELDS:
        raise ValueError("only a soft detector threshold can be calibrated")
    _fraction(max_false_positive_rate, "max_false_positive_rate")
    _fraction(min_recall, "min_recall")
    if not isinstance(validation_cases, tuple) or not isinstance(regression_cases, tuple):
        raise ValueError("evaluation inputs must be immutable tuples")
    if not 2 <= len(validation_cases) <= _MAX_CASES:
        raise ValueError("validation needs 2 to 10000 reviewed cases")
    if not 2 <= len(regression_cases) <= _MAX_CASES:
        raise ValueError("regression needs positive and negative hard-boundary cases")
    if any(not isinstance(case, ReviewedCase) for case in validation_cases):
        raise ValueError("validation cases must be ReviewedCase records")
    if any(case.split != "validation" for case in validation_cases):
        raise ValueError("calibration accepts validation split only; test/train data is forbidden")
    if any(not isinstance(case, RegressionCase) for case in regression_cases):
        raise ValueError("regression cases must be RegressionCase records")
    if any(case.candidate_version != candidate_version for case in regression_cases):
        raise ValueError("regression observations are bound to the candidate version")
    all_ids = [case.case_id for case in validation_cases + regression_cases]
    if len(set(all_ids)) != len(all_ids):
        raise ValueError("case identifiers must be unique across datasets")
    groups = {case.template_group for case in validation_cases}
    regression_groups = {case.template_group for case in regression_cases}
    if groups & regression_groups:
        raise ValueError("validation and regression template groups must be independent")
    if (
        not isinstance(excluded_template_groups, tuple)
        or len(excluded_template_groups) > _MAX_CASES
    ):
        raise ValueError("excluded template groups must be a bounded immutable tuple")
    for group in excluded_template_groups:
        _text(group, "excluded template group")
    if (groups | regression_groups) & set(excluded_template_groups):
        raise ValueError("train/test template group overlap is forbidden")
    if not {case.label for case in validation_cases} == {False, True}:
        raise ValueError("validation must include normal and risky cases")
    if not {case.expected_block for case in regression_cases} == {False, True}:
        raise ValueError("regression must include normal and forbidden actions")
    if not isinstance(thresholds, tuple) or not 1 <= len(thresholds) <= 256:
        raise ValueError("thresholds must contain 1 to 256 immutable candidates")
    for threshold in thresholds:
        _fraction(threshold, "threshold")
        if threshold == 0:
            raise ValueError("thresholds must be positive")
    thresholds = tuple(sorted(set(thresholds)))
    positives = sum(case.label for case in validation_cases)
    negatives = len(validation_cases) - positives
    metrics: list[tuple[float, float, float]] = []
    for threshold in thresholds:
        recall = sum(case.label and case.risk_score >= threshold for case in validation_cases)
        false_positives = sum(
            not case.label and case.risk_score >= threshold for case in validation_cases
        )
        metrics.append((recall / positives, false_positives / negatives, threshold))
    eligible = [
        row for row in metrics if row[0] >= min_recall and row[1] <= max_false_positive_rate
    ]
    # Prefer recall, then fewer false alarms; the highest tied threshold is stable.
    recall, false_positive_rate, threshold = max(
        eligible or metrics, key=lambda row: (row[0], -row[1], row[2])
    )
    regression_passed = all(case.expected_block == case.blocked for case in regression_cases)
    candidate = replace(base_policy, version=candidate_version, **{threshold_field: threshold})
    data_digest = _digest(
        {
            "validation": [asdict(case) for case in validation_cases],
            "regression": [asdict(case) for case in regression_cases],
            "excluded_templates": excluded_template_groups,
        }
    )
    values = {
        "base_policy": asdict(base_policy),
        "candidate_policy": asdict(candidate),
        "threshold_field": threshold_field,
        "thresholds": thresholds,
        "data_digest": data_digest,
        "max_false_positive_rate": max_false_positive_rate,
        "min_recall": min_recall,
        "false_positive_rate": false_positive_rate,
        "recall": recall,
        "regression_passed": regression_passed,
        "accepted": bool(eligible) and regression_passed,
    }
    return PolicyEvaluation(
        evaluation_id="evaluation:" + _digest(values),
        base_policy=base_policy,
        candidate_policy=candidate,
        threshold_field=threshold_field,
        thresholds=thresholds,
        validation_cases=validation_cases,
        regression_cases=regression_cases,
        excluded_template_groups=excluded_template_groups,
        max_false_positive_rate=max_false_positive_rate,
        min_recall=min_recall,
        false_positive_rate=false_positive_rate,
        recall=recall,
        regression_passed=regression_passed,
        accepted=bool(eligible) and regression_passed,
        data_digest=data_digest,
    )


@dataclass(frozen=True, slots=True)
class PolicyAuditRecord:
    sequence: int
    action: str
    version: str
    actor: str
    occurred_at: str
    evaluation_id: str | None
    previous_digest: str
    digest: str


class PolicyRegistry:
    """Offline promotion registry with explicit approval and published-only rollback.

    The initial policy is an operator-selected baseline, not a claim of measured
    accuracy. Callers must authenticate approval identities outside this module.
    Exported history is replay-validated but not signed; store it in a protected
    location or the application's authenticated audit store for tamper resistance.
    """

    def __init__(self, initial_policy: DetectorPolicy, *, initialized_by: str) -> None:
        if not isinstance(initial_policy, DetectorPolicy):
            raise ValueError("initial_policy must be a DetectorPolicy")
        _text(initialized_by, "initialized_by")
        self._initial = initial_policy
        self._policies = {initial_policy.version: initial_policy}
        self._evaluations: dict[str, PolicyEvaluation] = {}
        self._latest: dict[str, str] = {}
        self._approved: dict[str, str] = {}
        self._published = {initial_policy.version}
        self._active_version = initial_policy.version
        self._history: list[PolicyAuditRecord] = []
        self._append("INITIALIZED", initial_policy.version, initialized_by, None)

    @property
    def active_policy(self) -> DetectorPolicy:
        return self._policies[self._active_version]

    @property
    def history(self) -> tuple[PolicyAuditRecord, ...]:
        return tuple(self._history)

    @property
    def evaluations(self) -> tuple[PolicyEvaluation, ...]:
        return tuple(self._evaluations.values())

    def _room(self) -> None:
        if len(self._history) >= _MAX_EVENTS:
            raise ValueError("audit history limit reached; archive before further changes")

    def _append(self, action: str, version: str, actor: str, evaluation_id: str | None) -> None:
        _text(actor, "actor")
        self._room()
        data = {
            "sequence": len(self._history) + 1,
            "action": action,
            "version": version,
            "actor": actor,
            "occurred_at": datetime.now(UTC).isoformat(),
            "evaluation_id": evaluation_id,
            "previous_digest": self._history[-1].digest if self._history else "",
        }
        self._history.append(PolicyAuditRecord(**data, digest=_digest(data)))

    def stage(self, evaluation: PolicyEvaluation, *, actor: str) -> None:
        """Record passing or rejected evaluations; never publish here."""

        _text(actor, "actor")
        self._room()
        if not isinstance(evaluation, PolicyEvaluation):
            raise ValueError("evaluation must be a PolicyEvaluation")
        verified = calibrate_policy(
            evaluation.base_policy,
            candidate_version=evaluation.candidate_policy.version,
            validation_cases=evaluation.validation_cases,
            regression_cases=evaluation.regression_cases,
            threshold_field=evaluation.threshold_field,
            thresholds=evaluation.thresholds,
            max_false_positive_rate=evaluation.max_false_positive_rate,
            min_recall=evaluation.min_recall,
            excluded_template_groups=evaluation.excluded_template_groups,
        )
        if verified != evaluation:
            raise ValueError("evaluation data, selected policy or metrics were modified")
        base = evaluation.base_policy
        if base.version not in self._published or self._policies.get(base.version) != base:
            raise ValueError("candidate must be derived from a known published baseline")
        version = evaluation.candidate_policy.version
        if version in self._published:
            raise ValueError("a published version is immutable")
        previous = self._policies.get(version)
        if previous is not None and previous != evaluation.candidate_policy:
            raise ValueError("changed policy settings require a new version")
        if (
            evaluation.evaluation_id not in self._evaluations
            and len(self._evaluations) >= _MAX_RECORDS
        ):
            raise ValueError("evaluation limit reached; archive the registry")
        if version not in self._policies and len(self._policies) >= _MAX_RECORDS:
            raise ValueError("policy limit reached; archive the registry")
        previous_evaluation = self._latest.get(version)
        if previous_evaluation != evaluation.evaluation_id and previous_evaluation is not None:
            # A later reversion to old input data must not revive an old approval.
            self._approved.pop(previous_evaluation, None)
        self._policies[version] = evaluation.candidate_policy
        self._evaluations[evaluation.evaluation_id] = evaluation
        self._latest[version] = evaluation.evaluation_id
        self._append("EVALUATED", version, actor, evaluation.evaluation_id)

    def approve(self, evaluation_id: str, *, reviewer: str) -> None:
        _text(reviewer, "reviewer")
        self._room()
        evaluation = self._current_evaluation(evaluation_id)
        if not evaluation.accepted:
            raise ValueError("failed validation or regression cannot be approved")
        if evaluation.candidate_policy.version in self._published:
            raise ValueError("published policy does not accept new approval")
        self._approved[evaluation_id] = reviewer
        self._append("APPROVED", evaluation.candidate_policy.version, reviewer, evaluation_id)

    def _current_evaluation(self, evaluation_id: str) -> PolicyEvaluation:
        evaluation = self._evaluations.get(evaluation_id)
        if evaluation is None:
            raise ValueError("unknown evaluation")
        if self._latest.get(evaluation.candidate_policy.version) != evaluation_id:
            raise ValueError("evaluation was superseded; renewed review is required")
        return evaluation

    def publish(self, evaluation_id: str, *, actor: str) -> DetectorPolicy:
        _text(actor, "actor")
        self._room()
        evaluation = self._current_evaluation(evaluation_id)
        if not evaluation.accepted or evaluation_id not in self._approved:
            raise ValueError("publication requires successful evaluation and named human approval")
        if evaluation.base_policy != self.active_policy:
            raise ValueError("active baseline changed; recalibration and review are required")
        version = evaluation.candidate_policy.version
        if version in self._published:
            raise ValueError("version is already published; use explicit rollback to reactivate it")
        self._published.add(version)
        self._active_version = version
        self._append("PUBLISHED", version, actor, evaluation_id)
        return self.active_policy

    def rollback(self, version: str, *, actor: str) -> DetectorPolicy:
        _text(actor, "actor")
        self._room()
        if version not in self._published:
            raise ValueError("rollback target must be a previously published policy")
        if version == self._active_version:
            raise ValueError("target policy is already active")
        self._active_version = version
        self._append("ROLLED_BACK", version, actor, None)
        return self.active_policy

    def to_json(self) -> str:
        """Export original inputs and append-only promotion history for replay."""

        data = {
            "schema_version": 1,
            "initial_policy": asdict(self._initial),
            "evaluations": [asdict(item) for item in self._evaluations.values()],
            "history": [asdict(item) for item in self._history],
            "active_version": self._active_version,
        }
        serialized = json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(serialized) > 32 * 1024 * 1024:
            raise ValueError("registry export exceeds 32 MiB character limit; archive evaluations")
        return serialized

    @classmethod
    def from_json(cls, serialized: str) -> PolicyRegistry:
        """Replay all validation, approval and promotion conditions on restore."""

        if not isinstance(serialized, str) or len(serialized) > 32 * 1024 * 1024:
            raise ValueError("registry export exceeds 32 MiB character limit")
        try:
            data = json.loads(serialized)
            if set(data) != {
                "schema_version",
                "initial_policy",
                "evaluations",
                "history",
                "active_version",
            }:
                raise ValueError("unexpected registry fields")
            if data["schema_version"] != 1:
                raise ValueError("unsupported registry schema")
            if (
                not 1 <= len(data["history"]) <= _MAX_EVENTS
                or len(data["evaluations"]) > _MAX_RECORDS
            ):
                raise ValueError("registry record limits exceeded")
            evaluations: dict[str, PolicyEvaluation] = {}
            for raw in data["evaluations"]:
                values = dict(raw)
                values["base_policy"] = DetectorPolicy(**values["base_policy"])
                values["candidate_policy"] = DetectorPolicy(**values["candidate_policy"])
                values["validation_cases"] = tuple(
                    ReviewedCase(**row) for row in values["validation_cases"]
                )
                values["regression_cases"] = tuple(
                    RegressionCase(**row) for row in values["regression_cases"]
                )
                values["thresholds"] = tuple(values["thresholds"])
                values["excluded_template_groups"] = tuple(values["excluded_template_groups"])
                evaluation = PolicyEvaluation(**values)
                if evaluation.evaluation_id in evaluations:
                    raise ValueError("duplicate evaluation identity")
                evaluations[evaluation.evaluation_id] = evaluation
            records = tuple(PolicyAuditRecord(**raw) for raw in data["history"])
            initial = DetectorPolicy(**data["initial_policy"])
            registry = cls(initial, initialized_by=records[0].actor)
            previous_digest = ""
            used_evaluations: set[str] = set()
            for index, record in enumerate(records, start=1):
                values = asdict(record)
                claimed_digest = values.pop("digest")
                if record.sequence != index or record.previous_digest != previous_digest:
                    raise ValueError("audit sequence or digest chain mismatch")
                if _digest(values) != claimed_digest:
                    raise ValueError("audit record digest mismatch")
                instant = datetime.fromisoformat(record.occurred_at)
                if instant.tzinfo is None or instant.utcoffset() is None:
                    raise ValueError("audit time must include a timezone")
                _text(record.actor, "actor")
                if index == 1:
                    if (
                        record.action != "INITIALIZED"
                        or record.version != initial.version
                        or record.evaluation_id is not None
                    ):
                        raise ValueError("invalid initial audit record")
                elif record.action == "EVALUATED":
                    if record.evaluation_id is None:
                        raise ValueError("evaluation audit record requires an evaluation identity")
                    evaluation = evaluations[record.evaluation_id]
                    if evaluation.candidate_policy.version != record.version:
                        raise ValueError("evaluation and audit policy versions differ")
                    registry.stage(evaluation, actor=record.actor)
                    used_evaluations.add(evaluation.evaluation_id)
                elif record.action in {"APPROVED", "PUBLISHED"}:
                    if record.evaluation_id is None:
                        raise ValueError("approval/publication requires an evaluation identity")
                    evaluation = evaluations[record.evaluation_id]
                    if evaluation.candidate_policy.version != record.version:
                        raise ValueError("approval/publication policy version mismatch")
                    if record.action == "APPROVED":
                        registry.approve(evaluation.evaluation_id, reviewer=record.actor)
                    else:
                        registry.publish(evaluation.evaluation_id, actor=record.actor)
                elif record.action == "ROLLED_BACK":
                    if record.evaluation_id is not None:
                        raise ValueError("rollback must reference an existing published version")
                    registry.rollback(record.version, actor=record.actor)
                else:
                    raise ValueError("unsupported audit action")
                # Replace only the just-replayed record to preserve original time/hash.
                registry._history[-1] = record
                previous_digest = record.digest
            if (
                used_evaluations != set(evaluations)
                or registry._active_version != data["active_version"]
            ):
                raise ValueError("unreferenced evaluations or inconsistent active version")
            return registry
        except (KeyError, TypeError, AttributeError, OverflowError) as exc:
            raise ValueError("invalid registry state") from exc
