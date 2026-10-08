"""Bounded, prefix-only intent analysis independent of the execution gateway.

This module proposes a disposition. It cannot execute tools, update a contract,
approve an action, or treat text in a source as authorization.
"""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import posixpath
import re
import threading
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from fnmatch import fnmatchcase
from typing import Protocol

from ._types import (
    Assessment,
    DetectionInput,
    Disposition,
    ObservedBehavior,
    RecoveryContext,
    SourceRecord,
)
from .policy import DetectorPolicy
from .semantic import LightweightSemanticModel, SemanticResult

_EFFECT_LEVEL = {"none": 0, "write": 1, "delete": 2, "send": 3, "execute": 3}
_DIRECTIVE = re.compile(
    r"ignore (?:all |previous |the )*(?:instructions|rules|user)|"
    r"bypass|disable (?:audit|logging|security)|send .{0,80}(?:instead|external)|"
    r"忽略.{0,20}(?:指令|要求|用户)|绕过.{0,12}(?:审批|检测|限制)|"
    r"(?:删除|隐藏|省略).{0,16}(?:证据|日志|风险)|"
    r"(?:无需|不用).{0,8}(?:确认|授权)|改为.{0,20}(?:发送|修改|删除)",
    re.IGNORECASE,
)
_EVIDENCE_TAMPERING = re.compile(
    r"(?:删除|隐藏|省略|移除).{0,12}(?:反证|不利证据|故障记录)|"
    r"(?:delete|hide|omit|remove).{0,25}(?:counter.?evidence|contradictory evidence)",
    re.IGNORECASE,
)


class SemanticScorer(Protocol):
    @property
    def version(self) -> str: ...

    def score(
        self,
        *,
        goal: str,
        subgoal: str,
        tool: str,
        action: str,
        target: str,
        parameters_text: str = "",
    ) -> SemanticResult: ...


@dataclass(slots=True)
class _TaskState:
    events: deque[ObservedBehavior] = field(default_factory=deque)
    seen: deque[str] = field(default_factory=deque)
    last_step: int = -1
    no_progress: int = 0
    repeated_failures: int = 0
    last_failure: tuple[str, str, str] | None = None
    cumulative_drift: float = 0.0


def _normalize_target(value: str) -> str:
    value = value.strip().replace("\\", "/")
    if not value or "\x00" in value:
        return ""
    # Targets arrive normalized from the gateway. Collapse traversal before
    # comparing local resources; external addresses must match authorized values.
    if "://" in value:
        return value
    trailing = value.endswith("/")
    result = posixpath.normpath(value)
    if len(result) >= 2 and result[1] == ":":
        result = result.casefold()
    return result.rstrip("/") + "/" if trailing and result != "/" else result


def target_in_scope(target: str, scopes: tuple[str, ...]) -> bool:
    """Directory scopes end in /; literals and glob patterns are otherwise exact."""
    normalized = _normalize_target(target)
    if not normalized:
        return False
    for scope in scopes:
        pattern = _normalize_target(scope)
        if not pattern:
            continue
        if pattern.endswith("/"):
            if normalized == pattern.rstrip("/") or normalized.startswith(pattern):
                return True
        elif fnmatchcase(normalized, pattern):
            return True
    return False


class IntentDetectorCore:
    """Private core. The public provider must adapt the frozen runtime context.

    No decision cache is used: constraints and current versions are checked on
    every call. Only the semantic scorer may cache bounded, identical inputs.
    """

    VERSION = "intent-core-1.0.0"

    def __init__(
        self, *, policy: DetectorPolicy | None = None, semantic_model: SemanticScorer | None = None
    ) -> None:
        self.policy = policy or DetectorPolicy(version="intent-policy-1.0.0")
        self.semantic_model = semantic_model or LightweightSemanticModel(
            cache_size=min(self.policy.cache_size, 2048)
        )
        if isinstance(self.semantic_model, LightweightSemanticModel):
            self.semantic_model.set_cache_limit(min(self.policy.cache_size, 2048))
        self._states: OrderedDict[str, _TaskState] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def version(self) -> str:
        return f"{self.VERSION}/{self.semantic_model.version}"

    @property
    def state_sizes(self) -> dict[str, int]:
        with self._lock:
            return {
                "tasks": len(self._states),
                "events": sum(len(state.events) for state in self._states.values()),
                "seen_events": sum(len(state.seen) for state in self._states.values()),
            }

    def clear(self, task_id: str | None = None) -> None:
        with self._lock:
            if task_id is None:
                self._states.clear()
                return
            prefix = task_id + "\0"
            for key in tuple(self._states):
                if key.startswith(prefix):
                    del self._states[key]

    def set_policy(self, policy: DetectorPolicy) -> None:
        """Install an approved registry snapshot; never edits task permissions."""
        with self._lock:
            self.policy = policy
            if isinstance(self.semantic_model, LightweightSemanticModel):
                self.semantic_model.set_cache_limit(min(policy.cache_size, 2048))
            self.clear()

    def analyze(self, context: DetectionInput, /) -> Assessment:
        with self._lock:
            return self._analyze(context)

    def _analyze(self, context: DetectionInput) -> Assessment:
        context.validate()
        contract, action, policy = context.contract, context.candidate, self.policy
        base_refs = (contract.evidence_ref, action.evidence_ref)
        hard = self._hard_checks(context)
        if hard:
            return self._result(
                context, Disposition.BLOCK, 1.0, ("hard_constraint",), base_refs, hard[0], hard
            )
        if not contract.confirmed:
            return self._result(
                context,
                Disposition.REQUEST_CONFIRMATION,
                0.5,
                ("task_authorization",),
                base_refs,
                "TASK_NOT_CONFIRMED",
                ("任务目标尚未通过可信入口确认。",),
            )
        if (
            context.recovery_budget_exhausted
            or context.repeated_recovery
            or (context.replan_attempts > 0 and context.replan_attempts >= policy.max_replans)
        ):
            return self._result(
                context,
                Disposition.SAFE_TERMINATE,
                0.9,
                ("recovery_budget",),
                base_refs,
                "RECOVERY_BUDGET_EXHAUSTED",
                ("可信恢复记录表明预算耗尽或重复恢复状态，应停止任务。",),
            )

        state = self._update_state(context)
        dimensions: list[str] = []
        refs = list(base_refs)
        reasons: list[str] = []
        features: dict[str, float] = {}
        unsupported_target = bool(
            contract.goal_targets
            and not action.verified_support
            and any(
                target and not target_in_scope(target, contract.goal_targets)
                for target in (action.target, *action.additional_targets)
            )
        )
        features["unsupported_target"] = float(unsupported_target)

        semantic_score = 0.0
        semantic_confidence = 0.0
        if policy.enable_semantic:
            try:
                semantic = self.semantic_model.score(
                    goal=contract.goal,
                    subgoal=action.subgoal,
                    tool=action.tool,
                    action=action.action,
                    target=action.target,
                    parameters_text=action.parameters_text,
                )
                if (
                    not math.isfinite(semantic.score)
                    or not 0 <= semantic.score <= 1
                    or not math.isfinite(semantic.confidence)
                    or not 0 <= semantic.confidence <= 1
                ):
                    raise ValueError("non-finite or invalid semantic result")
                semantic_score, semantic_confidence = semantic.score, semantic.confidence
                if "input_truncated" in semantic.reason or semantic.reason == "insufficient_text":
                    return self._result(
                        context,
                        Disposition.REPLAN,
                        0.5,
                        ("evidence_gap",),
                        base_refs,
                        "SEMANTIC_EVIDENCE_INCOMPLETE",
                        ("语义输入不完整或超过编码预算，需分块核验后重新检测。",),
                    )
            except Exception as error:
                # A model fault is an availability/unknown result, not an attack label.
                return self._result(
                    context,
                    Disposition.REPLAN,
                    0.5,
                    ("detector_unavailable",),
                    base_refs,
                    "SEMANTIC_MODEL_UNAVAILABLE",
                    (f"语义检测未完成（{type(error).__name__}），暂停候选动作。",),
                )
        features["semantic_score"] = semantic_score
        features["semantic_confidence"] = semantic_confidence
        source_score, suspect_refs, source_reasons, missing_sources = self._sources(context)
        features["source_signal"] = source_score
        if suspect_refs:
            refs.extend(suspect_refs)
            dimensions.append("source_instruction")
            reasons.extend(source_reasons)

        sequence_score, sequence_dimensions, sequence_refs, sequence_reasons = (
            self._sequence(context, state) if policy.enable_sequence else (0.0, (), (), ())
        )
        features["sequence_score"] = sequence_score
        features["cumulative_drift"] = state.cumulative_drift
        dimensions.extend(sequence_dimensions)
        refs.extend(sequence_refs)
        reasons.extend(sequence_reasons)

        # Low similarity and merely encountering a source do not justify a stop.
        risk = max(
            semantic_score * semantic_confidence,
            sequence_score,
            0.35 if unsupported_target else 0.0,
            source_score * 0.35,
        )
        disposition = Disposition.CONTINUE
        reason_code = "WITHIN_TASK_SCOPE"

        # Describing an attack in a report is not execution of that attack. This
        # rule requires an actual deletion and an explicit deletion subgoal.
        tampering = (
            action.effect == "delete"
            and not action.verified_support
            and bool(_EVIDENCE_TAMPERING.match(action.subgoal[: policy.max_text_chars].lstrip()))
        )
        if tampering:
            risk = max(risk, 0.9)
            disposition, reason_code = Disposition.BLOCK, "EVIDENCE_MANIPULATION"
            dimensions.append("evidence_integrity")
            reasons.append("候选删除操作明确指向移除反证，需要保留原始证据。")
        elif missing_sources:
            risk = max(risk, 0.5)
            disposition, reason_code = Disposition.REPLAN, "SOURCE_EVIDENCE_INCOMPLETE"
            dimensions.append("evidence_gap")
            reasons.append("候选动作引用的来源无法完整核验，需补齐来源后重新检测。")
        elif policy.enable_sequence and sequence_score >= policy.sequence_threshold:
            disposition, reason_code = Disposition.REPLAN, "SEQUENCE_DRIFT"
            reasons.append("近期已执行行为与可信进展不匹配，建议在原授权范围内重规划。")
        elif (
            policy.enable_semantic
            and semantic_score >= policy.semantic_threshold
            and semantic_confidence >= 0.55
            and not action.verified_support
        ):
            disposition, reason_code = Disposition.REPLAN, "SEMANTIC_GOAL_MISMATCH"
            dimensions.append("goal_alignment")
            reasons.append("任务与候选子目标、工具动作及参数的联合分类提示目标偏离。")
        elif (
            unsupported_target
            and source_score >= 0.5
            and max(risk, 0.65) >= policy.confirmation_threshold
        ):
            risk = max(risk, 0.65)
            disposition, reason_code = Disposition.REQUEST_CONFIRMATION, "UNSUPPORTED_SCOPE_CHANGE"
            dimensions.append("goal_scope")
            reasons.append("新目标缺少已核验的任务依赖，并关联含指令性内容的外部来源。")
        elif unsupported_target and action.effect in {"write", "delete", "send", "execute"}:
            risk = max(risk, 0.65)
            disposition, reason_code = Disposition.REQUEST_CONFIRMATION, "UNSUPPORTED_SCOPE_CHANGE"
            dimensions.append("goal_scope")
            reasons.append("产生副作用的候选目标缺少任务依据，需要确认任务范围。")

        if not reasons:
            reasons.append("当前证据未达到偏移处置条件；后续动作继续逐步核验。")
        if disposition == Disposition.REPLAN and context.replan_attempts >= policy.max_replans:
            disposition, reason_code = Disposition.SAFE_TERMINATE, "RECOVERY_BUDGET_EXHAUSTED"
            dimensions.append("recovery_budget")
            reasons.append("当前偏移需要重规划，但允许的纠偏次数已耗尽。")
        return self._result(
            context,
            disposition,
            min(1.0, risk),
            tuple(dimensions),
            tuple(refs),
            reason_code,
            tuple(reasons),
            features,
            tuple(suspect_refs),
        )

    def _hard_checks(self, context: DetectionInput) -> tuple[str, ...]:
        contract, action = context.contract, context.candidate
        violations = list(context.hard_violations)
        names = {action.action.casefold(), action.tool.casefold(), action.effect.casefold()}
        if any(item.casefold() in names for item in contract.forbidden_actions):
            violations.append("FORBIDDEN_ACTION")
        if contract.allowed_tools is not None and action.tool not in contract.allowed_tools:
            violations.append("TOOL_OUTSIDE_CONTRACT")
        if contract.allowed_actions is not None and not names.intersection(
            item.casefold() for item in contract.allowed_actions
        ):
            violations.append("ACTION_OUTSIDE_CONTRACT")
        targets = (action.target, *action.additional_targets)
        if contract.resource_limits is not None and any(
            not target_in_scope(target, contract.resource_limits) for target in targets
        ):
            violations.append("RESOURCE_OUTSIDE_CONTRACT")
        if action.effect == "send":
            if not contract.allow_egress:
                violations.append("EGRESS_NOT_AUTHORIZED")
            elif not action.recipient or action.recipient not in contract.authorized_recipients:
                violations.append("RECIPIENT_NOT_AUTHORIZED")
        return tuple(dict.fromkeys(violations))

    def _update_state(self, context: DetectionInput) -> _TaskState:
        contract, action, policy = context.contract, context.candidate, self.policy
        serialized = json.dumps(asdict(contract), sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        key = "\0".join(
            (
                contract.task_id,
                contract.contract_id,
                contract.version,
                digest,
                policy.version,
                self.semantic_model.version,
            )
        )
        state = self._states.pop(key, _TaskState())
        if action.step_index <= state.last_step:
            # Replaying an earlier prefix must never inherit a future accumulator.
            state = _TaskState()
        self._states[key] = state
        while len(self._states) > policy.max_tasks:
            self._states.popitem(last=False)
        # Inputs from other tasks/versions and all current/future results are ignored.
        history = (
            event
            for event in context.history
            if event.task_id == contract.task_id
            and event.contract_version == contract.version
            and 0 <= event.step_index < action.step_index
            and event.status in {"succeeded", "failed"}
        )
        recent = heapq.nlargest(
            policy.window_size, history, key=lambda item: (item.step_index, item.event_id)
        )
        if any(
            event.step_index < state.last_step
            and hashlib.sha256(event.event_id.encode()).hexdigest() not in state.seen
            for event in recent
        ):
            # A trusted prefix may be filled in after an earlier evaluation.
            # Rebuild its bounded window instead of permanently losing late facts.
            merged = {event.event_id: event for event in state.events}
            merged.update((event.event_id, event) for event in recent)
            recent = heapq.nlargest(
                policy.window_size,
                merged.values(),
                key=lambda item: (item.step_index, item.event_id),
            )
            state = _TaskState()
            self._states[key] = state
        for event in sorted(recent, key=lambda item: (item.step_index, item.event_id)):
            event_digest = hashlib.sha256(event.event_id.encode()).hexdigest()
            if event_digest in state.seen or event.step_index < state.last_step:
                continue
            # Bound stored text even if a caller passes very large historical bodies.
            bounded = ObservedBehavior(
                event_id=event.event_id[:128],
                task_id=event.task_id,
                contract_version=event.contract_version,
                step_index=event.step_index,
                tool=event.tool[:256],
                action=event.action[:256],
                target=event.target[: policy.max_text_chars],
                evidence_ref=event.evidence_ref[:512],
                status=event.status,
                subgoal=event.subgoal[: policy.max_text_chars],
                source_refs=tuple(ref[:512] for ref in event.source_refs[: policy.max_sources]),
                effect=event.effect,
                verified_progress=event.verified_progress,
                verified_support=event.verified_support,
            )
            state.events.append(bounded)
            state.seen.append(event_digest)
            while len(state.events) > policy.window_size:
                state.events.popleft()
            while len(state.seen) > max(32, policy.window_size * 4):
                state.seen.popleft()
            state.last_step = max(state.last_step, event.step_index)
            if event.verified_progress and event.status == "succeeded":
                state.no_progress = 0
            elif event.verified_progress is False and not event.verified_support:
                state.no_progress = min(state.no_progress + 1, policy.max_no_progress * 2)
            fingerprint = (bounded.tool, bounded.action, bounded.target)
            if event.status == "failed":
                state.repeated_failures = (
                    state.repeated_failures + 1 if state.last_failure == fingerprint else 1
                )
                state.repeated_failures = min(
                    state.repeated_failures, policy.max_repeated_failures * 2
                )
                state.last_failure = fingerprint
            else:
                state.repeated_failures, state.last_failure = 0, None
            unsupported = bool(
                contract.goal_targets
                and not event.verified_support
                and not target_in_scope(event.target, contract.goal_targets)
            )
            state.cumulative_drift = 0.75 * state.cumulative_drift + 0.25 * float(unsupported)
        return state

    def _sources(
        self, context: DetectionInput
    ) -> tuple[float, tuple[str, ...], tuple[str, ...], bool]:
        if not self.policy.enable_sources:
            return 0.0, (), (), False
        action, policy = context.candidate, self.policy
        if len(action.source_refs) > policy.max_sources:
            return 0.0, (), (), True
        # Resolve only references actually used by this candidate, plus their parents.
        records = {record.ref: record for record in context.sources[: policy.max_sources * 4]}
        queue = deque((ref, frozenset()) for ref in action.source_refs)
        visited: set[str] = set()
        suspects: list[str] = []
        missing = False
        while queue and len(visited) < policy.max_sources:
            ref, ancestors = queue.popleft()
            if ref in ancestors:
                missing = True
                continue
            if ref in visited:
                continue
            visited.add(ref)
            source: SourceRecord | None = records.get(ref)
            if source is None:
                missing = True
                continue
            if source.source_type not in {"user", "external", "tool", "memory"}:
                missing = True
            if len(source.content) > policy.max_text_chars:
                missing = True
            if source.source_type != "user" and _DIRECTIVE.search(
                source.content[: policy.max_text_chars]
            ):
                suspects.append(ref)
            queue.extend(
                (parent, ancestors | {ref}) for parent in source.parent_refs[: policy.max_sources]
            )
            if len(source.parent_refs) > policy.max_sources:
                missing = True
        missing = missing or bool(queue)
        # A breadth-first traversal can encounter both ends of a cycle as roots.
        # Check the bounded graph separately; shared ancestors in a DAG are valid.
        colors: dict[str, int] = {}

        def cycle(ref: str) -> bool:
            if colors.get(ref) == 1:
                return True
            if colors.get(ref) == 2 or ref not in visited or ref not in records:
                return False
            colors[ref] = 1
            if any(cycle(parent) for parent in records[ref].parent_refs[: policy.max_sources]):
                return True
            colors[ref] = 2
            return False

        missing = missing or any(cycle(ref) for ref in visited)
        reasons = (
            ("关联来源包含指令性文本；来源关联仅作风险线索，不证明诱导因果。",) if suspects else ()
        )
        return (0.6 if suspects else 0.0), tuple(suspects), reasons, missing

    def _sequence(
        self, context: DetectionInput, state: _TaskState
    ) -> tuple[float, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
        policy, contract, action = self.policy, context.contract, context.candidate
        score = 0.0
        dimensions: list[str] = []
        reasons: list[str] = []
        refs: list[str] = []
        unsupported = [
            event
            for event in state.events
            if contract.goal_targets
            and not event.verified_support
            and not target_in_scope(event.target, contract.goal_targets)
        ]
        targets = {event.target for event in unsupported}
        if contract.goal_targets and not action.verified_support:
            targets.update(
                target
                for target in (action.target, *action.additional_targets)
                if target and not target_in_scope(target, contract.goal_targets)
            )
        if len(targets) >= 3:
            score = max(score, min(0.95, 0.3 * len(targets)))
            dimensions.append("resource_expansion")
            reasons.append("多个目标连续超出已核验的任务对象，存在持续扩张。")
            refs.extend(event.evidence_ref for event in unsupported)
        if len(unsupported) >= 3 and state.cumulative_drift >= policy.sequence_threshold:
            score = max(score, state.cumulative_drift)
            dimensions.append("persistent_goal_drift")
            refs.extend(event.evidence_ref for event in unsupported)
        if state.no_progress >= policy.max_no_progress:
            score = max(score, 0.75)
            dimensions.append("no_verified_progress")
            reasons.append("多步执行缺少核验进展及有效依赖依据，需重新核对计划。")
            refs.extend(event.evidence_ref for event in state.events)
        if state.repeated_failures >= policy.max_repeated_failures:
            # A verified alternative can recover from failure without extra confirmation.
            same_retry = state.last_failure == (action.tool, action.action, action.target)
            escalation = _EFFECT_LEVEL.get(action.effect, 0) > 0
            if same_retry or (escalation and not action.verified_support):
                score = max(score, 0.8)
                dimensions.append("repeated_failure")
                reasons.append("相同操作连续失败后继续重复或转向高影响动作，需受限重规划。")
                refs.extend(
                    event.evidence_ref for event in state.events if event.status == "failed"
                )
        levels = [_EFFECT_LEVEL.get(event.effect, 0) for event in unsupported]
        if len(levels) >= 2 and levels[-2] < levels[-1] < _EFFECT_LEVEL[action.effect]:
            score = max(score, 0.85)
            dimensions.append("impact_escalation")
            reasons.append("缺少任务依据的连续操作影响逐步升高。")
            refs.extend(event.evidence_ref for event in unsupported[-2:])
        return score, tuple(dimensions), tuple(refs), tuple(reasons)

    def _result(
        self,
        context: DetectionInput,
        disposition: Disposition,
        risk: float,
        dimensions: tuple[str, ...],
        refs: tuple[str, ...],
        reason_code: str,
        reasons: tuple[str, ...],
        features: dict[str, float] | None = None,
        contaminated_refs: tuple[str, ...] = (),
    ) -> Assessment:
        if disposition == Disposition.REPLAN and context.replan_attempts >= self.policy.max_replans:
            disposition, reason_code = Disposition.SAFE_TERMINATE, "RECOVERY_BUDGET_EXHAUSTED"
            dimensions = (*dimensions, "recovery_budget")
        return Assessment(
            disposition=disposition,
            risk_score=risk,
            trigger_dimensions=tuple(dict.fromkeys(dimensions)),
            evidence_refs=tuple(dict.fromkeys(refs))[: self.policy.max_evidence],
            reason_code=reason_code,
            reasons=reasons,
            policy_version=self.policy.version,
            detector_version=self.version,
            expires_at=context.now + timedelta(seconds=self.policy.decision_ttl_seconds),
            features=tuple(sorted((features or {}).items())),
            contaminated_refs=contaminated_refs[: self.policy.max_sources],
        )

    def recovery_context(self, context: DetectionInput, assessment: Assessment) -> RecoveryContext:
        """Reconstruct trusted constraints and evidence references, not source instructions."""
        context.validate()
        contract = context.contract
        refs = tuple(
            dict.fromkeys(
                event.evidence_ref
                for event in context.history
                if event.task_id == contract.task_id
                and event.contract_version == contract.version
                and event.step_index < context.candidate.step_index
                and event.status == "succeeded"
                and event.verified_progress
            )
        )
        return RecoveryContext(
            task_id=contract.task_id,
            contract_id=contract.contract_id,
            contract_version=contract.version,
            goal=contract.goal,
            allowed_tools=contract.allowed_tools,
            allowed_actions=contract.allowed_actions,
            resource_limits=contract.resource_limits,
            forbidden_actions=contract.forbidden_actions,
            success_criteria=contract.success_criteria,
            verified_progress_refs=refs[-self.policy.max_evidence :],
            excluded_source_refs=assessment.contaminated_refs,
            reason_codes=(assessment.reason_code,),
            remaining_attempts=max(0, self.policy.max_replans - context.replan_attempts),
        )
