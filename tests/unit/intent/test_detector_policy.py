"""Synthetic policy fixtures; reviewer names here are not real human approvals."""

import json
from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest

from ra_agent.intent.detection.policy import (
    DetectorPolicy,
    PolicyRegistry,
    RegressionCase,
    ReviewedCase,
    calibrate_policy,
)


def _cases() -> tuple[ReviewedCase, ...]:
    return tuple(
        ReviewedCase(
            case_id=f"validation-{index}",
            template_group=f"validation-template-{index}",
            split="validation",
            label=label,
            risk_score=score,
            reviewer="synthetic-reviewer-fixture",
            source="human_review",
        )
        for index, (label, score) in enumerate(
            [(False, 0.1), (False, 0.3), (True, 0.75), (True, 0.95)]
        )
    )


def _regression(version: str = "policy-2") -> tuple[RegressionCase, ...]:
    return tuple(
        RegressionCase(
            case_id=f"regression-{index}",
            template_group=f"regression-template-{index}",
            candidate_version=version,
            expected_block=blocked,
            blocked=blocked,
            reviewer="synthetic-independent-reviewer-fixture",
            source="reviewed_environment_evidence",
        )
        for index, blocked in enumerate((False, True))
    )


def _evaluation(**updates):
    values = {
        "candidate_version": "policy-2",
        "validation_cases": _cases(),
        "regression_cases": _regression(),
        "max_false_positive_rate": 0.0,
        "min_recall": 1.0,
    }
    values.update(updates)
    return calibrate_policy(DetectorPolicy("policy-1"), **values)


def _registry() -> PolicyRegistry:
    return PolicyRegistry(DetectorPolicy("policy-1"), initialized_by="fixture-operator")


@pytest.mark.parametrize(
    "field,value",
    [
        ("window_size", 0),
        ("window_size", 257),
        ("max_tasks", 1025),
        ("max_sources", 129),
        ("cache_size", -1),
        ("max_text_chars", 65537),
        ("max_evidence", 129),
        ("max_replans", 33),
        ("decision_ttl_seconds", 3601),
        ("max_no_progress", True),
        ("max_repeated_failures", 0),
        ("semantic_threshold", float("nan")),
        ("sequence_threshold", float("inf")),
        ("confirmation_threshold", -0.1),
        ("confirmation_threshold", True),
        ("semantic_threshold", 0),
        ("enable_sources", 1),
        ("version", " "),
    ],
)
def test_policy_rejects_unbounded_or_invalid_settings(field, value):
    values = {"version": "fixture-policy", field: value}
    with pytest.raises(ValueError):
        DetectorPolicy(**values)


def test_policy_is_immutable_and_has_no_hard_permission_fields():
    policy = DetectorPolicy("fixture-policy", cache_size=0, max_replans=0)
    with pytest.raises(FrozenInstanceError):
        setattr(policy, "semantic_threshold", 0.1)
    with pytest.raises(TypeError):
        unexpected: dict[str, Any] = {"allowed_actions": ["delete_file"]}
        DetectorPolicy("fixture-policy", **unexpected)


def test_calibration_selects_reproducible_threshold_with_both_constraints():
    evaluation = _evaluation()
    assert evaluation.accepted
    assert evaluation.candidate_policy.semantic_threshold == 0.75
    assert evaluation.recall == 1.0
    assert evaluation.false_positive_rate == 0.0
    assert evaluation == _evaluation()
    assert evaluation.data_digest


@pytest.mark.parametrize("split", ["test", "train"])
def test_only_validation_split_can_select_thresholds(split):
    cases = tuple(replace(case, split=split) for case in _cases())
    with pytest.raises(ValueError, match="validation split only"):
        _evaluation(validation_cases=cases)


def test_external_content_and_continue_click_cannot_supply_labels():
    for source in ("user_continue", "tool_output", "website", "memory", "agent"):
        with pytest.raises(ValueError, match="reviewed source"):
            replace(_cases()[0], source=source)
    with pytest.raises(ValueError, match="reviewer"):
        replace(_cases()[0], reviewer="")


def test_splits_require_distinct_ids_and_template_groups():
    regression = _regression()
    with pytest.raises(ValueError, match="unique"):
        _evaluation(
            regression_cases=(
                replace(regression[0], case_id=_cases()[0].case_id),
                regression[1],
            )
        )
    with pytest.raises(ValueError, match="independent"):
        _evaluation(
            regression_cases=(
                replace(regression[0], template_group=_cases()[0].template_group),
                regression[1],
            )
        )
    with pytest.raises(ValueError, match="overlap"):
        _evaluation(excluded_template_groups=(_cases()[0].template_group,))


def test_cannot_calibrate_hard_rules_or_use_another_candidate_regression():
    with pytest.raises(ValueError, match="soft detector threshold"):
        _evaluation(threshold_field="allowed_actions")
    with pytest.raises(ValueError, match="bound to the candidate"):
        _evaluation(regression_cases=_regression("different-candidate"))


def test_empty_or_one_class_dataset_is_not_valid_evidence():
    with pytest.raises(ValueError, match="normal and risky"):
        _evaluation(
            validation_cases=tuple(replace(case, label=False) for case in _cases())
        )
    with pytest.raises(ValueError, match="normal and forbidden"):
        _evaluation(
            regression_cases=tuple(
                replace(case, expected_block=True, blocked=True)
                for case in _regression()
            )
        )
    with pytest.raises(ValueError, match="regression needs"):
        _evaluation(regression_cases=())


def test_constraint_failure_is_recorded_and_cannot_be_approved():
    cases = tuple(replace(case, risk_score=0.9) for case in _cases())
    evaluation = _evaluation(validation_cases=cases)
    assert not evaluation.accepted
    registry = _registry()
    registry.stage(evaluation, actor="fixture-evaluator")
    assert registry.evaluations == (evaluation,)
    assert registry.active_policy.version == "policy-1"
    with pytest.raises(ValueError, match="cannot be approved"):
        registry.approve(evaluation.evaluation_id, reviewer="fixture-reviewer")


@pytest.mark.parametrize("index", [0, 1])
def test_any_hard_regression_failure_prevents_promotion(index):
    regression = list(_regression())
    regression[index] = replace(
        regression[index], blocked=not regression[index].blocked
    )
    evaluation = _evaluation(regression_cases=tuple(regression))
    assert not evaluation.regression_passed
    assert not evaluation.accepted
    registry = _registry()
    registry.stage(evaluation, actor="fixture-evaluator")
    with pytest.raises(ValueError):
        registry.approve(evaluation.evaluation_id, reviewer="fixture-reviewer")
    with pytest.raises(ValueError):
        registry.publish(evaluation.evaluation_id, actor="fixture-operator")


def test_publication_requires_named_approval_and_rollback_requires_published_version():
    registry = _registry()
    evaluation = _evaluation()
    registry.stage(evaluation, actor="fixture-evaluator")
    with pytest.raises(ValueError, match="named human approval"):
        registry.publish(evaluation.evaluation_id, actor="fixture-operator")
    with pytest.raises(ValueError, match="reviewer"):
        registry.approve(evaluation.evaluation_id, reviewer="")
    with pytest.raises(ValueError, match="previously published"):
        registry.rollback("policy-2", actor="fixture-operator")
    registry.approve(evaluation.evaluation_id, reviewer="fixture-reviewer")
    assert (
        registry.publish(evaluation.evaluation_id, actor="fixture-operator").version
        == "policy-2"
    )
    assert registry.rollback("policy-1", actor="fixture-operator").version == "policy-1"
    assert registry.rollback("policy-2", actor="fixture-operator").version == "policy-2"
    assert [record.action for record in registry.history] == [
        "INITIALIZED",
        "EVALUATED",
        "APPROVED",
        "PUBLISHED",
        "ROLLED_BACK",
        "ROLLED_BACK",
    ]
    with pytest.raises(FrozenInstanceError):
        setattr(registry.history[-1], "actor", "changed")


def test_changed_evaluation_data_invalidates_prior_approval():
    registry = _registry()
    old = _evaluation()
    registry.stage(old, actor="fixture-evaluator")
    registry.approve(old.evaluation_id, reviewer="fixture-reviewer")
    cases = (replace(_cases()[0], risk_score=0.11),) + _cases()[1:]
    updated = _evaluation(validation_cases=cases)
    assert old.candidate_policy == updated.candidate_policy
    assert old.data_digest != updated.data_digest
    registry.stage(updated, actor="fixture-evaluator")
    with pytest.raises(ValueError, match="superseded"):
        registry.publish(old.evaluation_id, actor="fixture-operator")
    with pytest.raises(ValueError, match="named human approval"):
        registry.publish(updated.evaluation_id, actor="fixture-operator")
    registry.approve(updated.evaluation_id, reviewer="fixture-reviewer-2")
    registry.publish(updated.evaluation_id, actor="fixture-operator")


def test_forged_evaluation_metrics_and_policy_are_recomputed():
    registry = _registry()
    evaluation = _evaluation()
    for modified in (
        replace(evaluation, recall=0.0),
        replace(evaluation, data_digest="modified"),
        replace(
            evaluation,
            candidate_policy=replace(evaluation.candidate_policy, enable_sources=False),
        ),
    ):
        with pytest.raises(ValueError, match="modified"):
            registry.stage(modified, actor="fixture-evaluator")


def test_reverting_evaluation_data_does_not_revive_old_approval():
    registry = _registry()
    old = _evaluation()
    registry.stage(old, actor="fixture-evaluator")
    registry.approve(old.evaluation_id, reviewer="fixture-reviewer")
    updated = _evaluation(
        validation_cases=(replace(_cases()[0], risk_score=0.11),) + _cases()[1:]
    )
    registry.stage(updated, actor="fixture-evaluator")
    registry.stage(old, actor="fixture-evaluator")
    with pytest.raises(ValueError, match="named human approval"):
        registry.publish(old.evaluation_id, actor="fixture-operator")
    restored = PolicyRegistry.from_json(registry.to_json())
    with pytest.raises(ValueError, match="named human approval"):
        restored.publish(old.evaluation_id, actor="fixture-operator")


def test_changed_active_baseline_requires_recalibration():
    registry = _registry()
    evaluation = _evaluation()
    alternative = _evaluation(
        candidate_version="policy-3", regression_cases=_regression("policy-3")
    )
    for item in (evaluation, alternative):
        registry.stage(item, actor="fixture-evaluator")
        registry.approve(item.evaluation_id, reviewer="fixture-reviewer")
    registry.publish(evaluation.evaluation_id, actor="fixture-operator")
    with pytest.raises(ValueError, match="active baseline changed"):
        registry.publish(alternative.evaluation_id, actor="fixture-operator")


def test_json_round_trip_replays_approval_publish_and_rollback():
    registry = _registry()
    evaluation = _evaluation()
    registry.stage(evaluation, actor="fixture-evaluator")
    registry.approve(evaluation.evaluation_id, reviewer="fixture-reviewer")
    registry.publish(evaluation.evaluation_id, actor="fixture-operator")
    registry.rollback("policy-1", actor="fixture-operator")
    restored = PolicyRegistry.from_json(registry.to_json())
    assert restored.to_json() == registry.to_json()
    assert restored.active_policy == registry.active_policy
    assert restored.history == registry.history
    restored.rollback("policy-2", actor="fixture-operator")


@pytest.mark.parametrize("change", ["metric", "data", "approval", "active", "extra"])
def test_restore_rejects_modified_data_or_invalid_history(change):
    registry = _registry()
    evaluation = _evaluation()
    registry.stage(evaluation, actor="fixture-evaluator")
    registry.approve(evaluation.evaluation_id, reviewer="fixture-reviewer")
    registry.publish(evaluation.evaluation_id, actor="fixture-operator")
    data = json.loads(registry.to_json())
    if change == "metric":
        data["evaluations"][0]["recall"] = 0.1
    elif change == "data":
        data["evaluations"][0]["validation_cases"][0]["risk_score"] = 0.2
    elif change == "approval":
        data["history"][2]["actor"] = "changed"
    elif change == "active":
        data["active_version"] = "never-approved-policy"
    else:
        data["extra"] = "unexpected"
    with pytest.raises(ValueError):
        PolicyRegistry.from_json(json.dumps(data))


def test_restore_rejects_test_labels_even_if_caller_rewrites_serialized_data():
    registry = _registry()
    registry.stage(_evaluation(), actor="fixture-evaluator")
    data = json.loads(registry.to_json())
    data["evaluations"][0]["validation_cases"][0]["split"] = "test"
    with pytest.raises(ValueError, match="validation split only"):
        PolicyRegistry.from_json(json.dumps(data))
