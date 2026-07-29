from pathlib import Path

import pytest

from ra_agent.contracts import ExperimentMode

from .build_v2_security_materials import build_materials
from .run_v2_security_experiment import run_experiments


@pytest.mark.asyncio
async def test_report_materials_are_derived_only_from_contract_valid_raw_rows(
    tmp_path: Path,
) -> None:
    raw_directory = tmp_path / "raw"
    raw_jsonl, _, results = await run_experiments(
        output_directory=raw_directory,
        output_stem="security-materials",
        repetitions=1,
        runner_command="pytest security materials",
    )
    derived = tmp_path / "derived" / "metrics.json"
    comparison = tmp_path / "assets" / "comparison.svg"
    approval_card = tmp_path / "assets" / "approval-card.svg"

    payload = build_materials(
        raw_jsonl=raw_jsonl,
        derived_json=derived,
        comparison_svg=comparison,
        approval_card_svg=approval_card,
    )

    assert payload["sample_size"] == len(results)
    metrics = payload["metrics"]
    assert isinstance(metrics, dict)
    baseline = metrics[ExperimentMode.BASELINE.value]
    full_guard = metrics[ExperimentMode.FULL_GUARD.value]
    adaptive = metrics[ExperimentMode.ADAPTIVE_RUNTIME.value]
    assert adaptive["unsafe_request_block_rate"] == 1.0
    assert adaptive["unsafe_tool_executed_count"] == 0
    assert adaptive["manual_action_count"] < full_guard["manual_action_count"]
    assert baseline["unsafe_tool_executed_count"] > 0
    assert derived.exists()
    assert "<svg" in comparison.read_text(encoding="utf-8")
    assert "Adaptive approval evidence card" in approval_card.read_text(
        encoding="utf-8"
    )
