import json

import pytest
from experiments.intent.policy_cycle import apply
from experiments.intent.review_gate import validate
from ra_agent.security.intent_policy import load_policy


def test_trusted_policy_reloads_and_preserves_all_checks(tmp_path):
    path = tmp_path / "policy.json"
    assert load_policy(path).repeat_limit == 3
    release = {
        "schema_version": "intent-policy-release-v1",
        "version": "reviewed:2",
        "repeat_limit": 2,
        "approved_by": "simulated-test-operator",
        "reviewers": ["simulated-a", "simulated-b"],
        "validation_ref": "test-fixture",
    }
    path.write_text(json.dumps(release), encoding="utf-8")
    policy = load_policy(path)
    assert (
        policy.repeat_limit == 2
        and policy.check_report
        and policy.check_source
        and policy.check_sequence
    )
    release["repeat_limit"] = 9
    path.write_text(json.dumps(release), encoding="utf-8")
    with pytest.raises(ValueError):
        load_policy(path)
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        load_policy(path)


def test_empty_human_review_cannot_release_policy(tmp_path):
    review = tmp_path / "review.csv"
    review.write_text(
        "case_id,status,reviewer_1,reviewer_2,reviewer_1_verdict,reviewer_2_verdict,disagreement,resolution,evidence_ref,reviewed_at\n",
        encoding="utf-8",
    )
    assert not validate(review)["valid"]
    with pytest.raises(ValueError, match="Human review gate"):
        apply(review, tmp_path / "feedback.csv", tmp_path, tmp_path, "operator")


def test_preset_rollback_does_not_invent_reviewers(tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "intent-policy-release-v1",
                "kind": "preset",
                "version": "intent-policy:1",
                "repeat_limit": 3,
            }
        ),
        encoding="utf-8",
    )
    assert load_policy(path).version == "intent-policy:1"


def test_release_and_rollback_preserve_versioned_snapshot(tmp_path, monkeypatch):
    import csv
    import hashlib

    from experiments.intent import policy_cycle
    from experiments.intent.dataset import load

    monkeypatch.setattr(policy_cycle, "ROOT", tmp_path)
    runtime = tmp_path / ".runtime/intent-integration/simulated-policy-test"
    runtime.mkdir(parents=True)
    (runtime / "state.sqlite3").touch()
    review = tmp_path / "review.csv"
    fields = [
        "case_id",
        "status",
        "reviewer_1",
        "reviewer_2",
        "reviewer_1_verdict",
        "reviewer_2_verdict",
        "disagreement",
        "resolution",
        "evidence_ref",
        "reviewed_at",
    ]
    with review.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for case in load():
            writer.writerow(
                {
                    "case_id": case["case_id"],
                    "status": "approved",
                    "reviewer_1": "simulated-test-a",
                    "reviewer_2": "simulated-test-b",
                    "reviewer_1_verdict": "agree",
                    "reviewer_2_verdict": "agree",
                    "evidence_ref": "simulated-test-fixture",
                    "reviewed_at": "2026-10-07",
                }
            )
    feedback = tmp_path / "feedback.csv"
    feedback.write_text(
        "feedback_id,case_id,reviewer,evaluation_ref,approval,approved_by\ntest-feedback,normal-0-0,simulated-test-a,simulated-test-fixture,approved,test-operator\n",
        encoding="utf-8",
    )
    run = tmp_path / "synthetic-gate-unit-fixture"
    run.mkdir()
    summary = {
        "arms": {
            "full": {
                "completed_runs": 40,
                "normal_safe_success": 8,
                "safe_stops": 24,
                "forbidden_effect_trajectories": 0,
            }
        },
        "repeat_limit": 2,
        "source_unchanged_during_run": True,
        "failures": [],
    }
    (run / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (run / "checksums.json").write_text(
        json.dumps(
            {
                "summary.json": hashlib.sha256(
                    (run / "summary.json").read_bytes()
                ).hexdigest()
            }
        ),
        encoding="utf-8",
    )
    release = policy_cycle.apply(review, feedback, run, runtime, "test-operator")
    assert load_policy(runtime / "intent-policy.json").repeat_limit == 2
    assert release["rollback_sha256"]
    policy_cycle.rollback(runtime, "test-operator")
    assert load_policy(runtime / "intent-policy.json").repeat_limit == 3
    assert (
        json.loads(
            (runtime / "policy-rollback-receipt.json").read_text(encoding="utf-8")
        )["to"]
        == "intent-policy:1"
    )
