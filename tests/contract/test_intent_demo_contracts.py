import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ra_agent.intent_demo.contracts import DecisionResult, IntentDemoSchema

ROOT = Path(__file__).resolve().parents[2]


def test_published_demo_schema_matches_local_models() -> None:
    published = json.loads(
        (ROOT / "docs/contracts/intent-demo-v0.1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert published == IntentDemoSchema.model_json_schema()
    expected = {
        "IntentSpec": "intent_id task_id goal scope allowed_actions forbidden_actions "
        "success_criteria source_refs version confirmed_by confirmed_at",
        "TaskContract": "contract_id contract_version intent_ref resources tools permissions "
        "completion_conditions parent_version status digest",
        "BehaviorEvent": "event_id task_id step_index actor source_type source_ref subgoal tool "
        "action args_digest target risk_flags timestamp",
        "DecisionResult": "decision_id decision risk_score trigger_dimensions evidence_refs "
        "reason_code policy_version detector_version expires_at",
        "CorrectionPlan": "plan_id base_contract_version contaminated_refs proposed_actions "
        "added_scope confirmation_required retry_budget status",
        "EffectCheck": "effect_id request_id tool normalized_target before_digest after_digest "
        "side_effect_ref status",
    }
    for name, fields in expected.items():
        assert set(published["$defs"][name]["properties"]) == set(fields.split())
        assert published["$defs"][name]["additionalProperties"] is False


@pytest.mark.parametrize("value", ["UNKNOWN", "DENY", "continue"])
def test_demo_decisions_fail_closed_for_unknown_values(value: str) -> None:
    with pytest.raises(ValidationError):
        DecisionResult.model_validate({"decision": value})
