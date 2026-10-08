"""Reproduction harness separation, artifact loading and resource bound checks."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest
from ra_agent.intent.detection._types import DetectionInput
from ra_agent.intent.detection.engine import IntentDetectorCore
from scripts.intent_detector import (
    MODES,
    load_json,
    policy_for_mode,
    run_benchmark,
    run_replay,
    run_train,
    write_json,
)

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests" / "fixtures" / "intent" / "detector_replay_smoke.json"


def training_rows() -> dict:
    return {
        "split": "train",
        "examples": [
            {"split": "train", "label": 0, "goal": "分析设备配置", "subgoal": "读取设备配置"},
            {"split": "train", "label": 1, "goal": "分析设备配置", "subgoal": "删除无关证据"},
        ],
    }


def test_training_rejects_test_rows_and_does_not_relabel_them() -> None:
    rows = training_rows()
    rows["examples"][0]["split"] = "test"
    with pytest.raises(ValueError, match="train split"):
        run_train(rows, epochs=1, dimensions=128)
    rows = training_rows()
    rows["split"] = "test"
    with pytest.raises(ValueError, match="split='train'"):
        run_train(rows, epochs=1, dimensions=128)


@pytest.mark.parametrize("mode", MODES)
def test_replay_modes_have_honest_metadata_and_finite_output(mode: str) -> None:
    result = run_replay(load_json(FIXTURE), mode=mode)
    assert result["mode"] == mode
    assert result["semantic_model_version"] == "lexical-baseline-v1"
    assert result["scope"]["actual_side_effect_prevention_measured"] is False
    assert result["comparison"]["expected_matches"] == result["comparison"]["expected_comparisons"]
    assert result["policy"]["enable_sequence"] == (mode in {"full", "no-sources"})
    assert result["policy"]["enable_sources"] == (mode in {"single", "full"})
    json.dumps(result, allow_nan=False)


def test_replay_passes_only_observed_prefix_and_labels_do_not_change_decisions() -> None:
    contexts: list[DetectionInput] = []

    class RecordingCore(IntentDetectorCore):
        def analyze(self, context: DetectionInput, /):
            contexts.append(context)
            return super().analyze(context)

    data = load_json(FIXTURE)
    original = run_replay(data, core=RecordingCore(policy=policy_for_mode("full")))
    for context in contexts:
        assert all(event.step_index < context.candidate.step_index for event in context.history)
        assert not {"label", "expected_decision", "scenario"} & {
            field.name for field in fields(context)
        }
    expand = [c for c in contexts if c.contract.task_id == "expand"]
    assert [len(context.history) for context in expand] == [0, 1, 2]
    changed = copy.deepcopy(data)
    for case in changed["cases"]:
        for step in case["steps"]:
            step["label"] = not step.get("label", False)
            step["expected_decision"] = "BLOCK"
    replayed = run_replay(changed)
    for before, after in zip(original["steps"], replayed["steps"], strict=True):
        assert {k: v for k, v in before.items() if k != "offline_comparison"} == {
            k: v for k, v in after.items() if k != "offline_comparison"
        }
    # A later outcome must not alter an earlier score or evidence set.
    changed = copy.deepcopy(data)
    changed["cases"][2]["steps"][1]["observed"]["target"] = "future-only-device"
    changed_result = run_replay(changed)
    assert original["steps"][:4] == changed_result["steps"][:4]


def test_benchmark_bounds_counts_and_memory_scopes() -> None:
    result = run_benchmark(steps=30, tasks=10, workers=3, max_tasks=2, window_size=2, cache_size=3)
    assert result["single_task"]["calls"] == 30
    assert result["concurrent_tasks"]["calls"] == 30
    assert result["concurrent_tasks"]["tasks"] == 10
    assert result["retained"]["tasks"] <= 2
    assert result["retained"]["events"] <= 4
    assert result["retained"]["cache_entries"] <= 3
    assert result["bounds_satisfied"] is True
    assert result["python_allocation_peak"]["method"] != result["process_memory_peak"]["method"]
    assert result["scope"]["official_500_mb_compliance_established"] is False
    json.dumps(result, allow_nan=False)
    with pytest.raises(ValueError, match="steps"):
        run_benchmark(steps=20001)


def test_cli_unicode_training_and_replay_load_explicit_model(tmp_path: Path) -> None:
    folder = tmp_path / "中文 路径"
    training, artifact, report = folder / "训练.json", folder / "模型.json", folder / "结果.json"
    write_json(training_rows(), training)
    for arguments in (
        [
            "train",
            "--input",
            str(training),
            "--output",
            str(artifact),
            "--epochs",
            "1",
            "--dimensions",
            "128",
        ],
        ["replay", "--input", str(FIXTURE), "--model", str(artifact), "--output", str(report)],
    ):
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "intent_detector.py"), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
    assert load_json(report)["semantic_model_version"].startswith("intent-linear-v1@")


def test_json_rejects_non_finite_and_duplicate_keys(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    for invalid in ('{"x": NaN}', '{"x": 1, "x": 2}'):
        path.write_text(invalid, encoding="utf-8")
        with pytest.raises(ValueError):
            load_json(path)
