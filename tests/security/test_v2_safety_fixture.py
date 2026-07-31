import json
from pathlib import Path

import pytest

from experiments.v2.runners.run_safety_evaluation import security_cases


def test_frozen_safety_fixture_has_unique_complete_ground_truth() -> None:
    cases = security_cases()

    assert len(cases) == len({case.case_id for case in cases})
    assert {case.ground_truth for case in cases} == {"SAFE", "UNSAFE"}
    assert {case.approval_action for case in cases} == {"grant", "deny", "expire"}
    assert all(
        (case.ground_truth == "SAFE") == (case.expected_status == "SUCCESS")
        for case in cases
    )


@pytest.mark.parametrize(
    "payload, expected_error",
    [
        ({"schema_version": 2, "cases": []}, "schema_version"),
        ({"schema_version": 1, "cases": []}, "non-empty"),
        (
            {
                "schema_version": 1,
                "cases": [
                    {
                        "case_id": "bad-ground-truth",
                        "family": "test",
                        "tool_name": "read_file",
                        "arguments": {"path": "docs"},
                        "source_type": "user",
                        "expected_status": "SUCCESS",
                        "approval_action": "grant",
                        "ground_truth": "UNSAFE",
                    }
                ],
            },
            "disagree",
        ),
    ],
)
def test_invalid_safety_fixture_fails_closed(
    tmp_path: Path,
    payload: dict[str, object],
    expected_error: str,
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=expected_error):
        security_cases(fixture)
