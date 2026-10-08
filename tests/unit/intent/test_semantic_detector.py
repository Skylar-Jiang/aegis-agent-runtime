"""Offline semantic scorer tests; synthetic training is not effectiveness evidence."""

import hashlib
import json

import pytest

from ra_agent.intent.detection.semantic import (
    BASELINE_VERSION,
    LightweightSemanticModel,
    SemanticModelError,
    train_linear_model,
)


def _rows():
    return [
        {
            "split": "train",
            "label": 0,
            "goal": "分析设备A配置并生成报告",
            "subgoal": "读取设备A配置",
            "tool": "read_config",
            "action": "read",
            "target": "device-A",
        },
        {
            "split": "train",
            "label": 1,
            "goal": "分析设备A配置并生成报告",
            "subgoal": "删除设备A日志",
            "tool": "delete_file",
            "action": "delete",
            "target": "device-A.log",
        },
        {
            "split": "train",
            "label": 0,
            "goal": "Analyze device A and write a risk report",
            "subgoal": "Read configuration evidence",
            "tool": "read_config",
            "action": "read",
            "target": "device-A",
        },
        {
            "split": "train",
            "label": 1,
            "goal": "Analyze device A and write a risk report",
            "subgoal": "Erase fault evidence",
            "tool": "delete_file",
            "action": "delete",
            "target": "device-A.log",
            "parameters_text": "remove all logs",
        },
    ]


def _write(tmp_path, artifact):
    path = tmp_path / "模型.json"
    path.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    return path


def _resign(artifact):
    payload = {key: value for key, value in artifact.items() if key != "sha256"}
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=True,
    ).encode("utf-8")
    return {**payload, "sha256": hashlib.sha256(canonical).hexdigest()}


def _score(model, **overrides):
    return model.score(
        **{
            "goal": "分析设备配置生成报告",
            "subgoal": "读取设备配置",
            "tool": "read_file",
            "action": "read",
            "target": "device-A",
            **overrides,
        }
    )


def test_lexical_baseline_is_explicit_and_retains_chinese_evidence():
    result = _score(LightweightSemanticModel())
    assert result.model_version == BASELINE_VERSION
    assert result.reason == "lexical_baseline_uncertain"
    assert "设备" in result.evidence_terms
    assert 0 <= result.score <= 1
    assert result.confidence <= 0.2


def test_legitimate_intermediate_step_without_overlap_remains_uncertain():
    result = _score(
        LightweightSemanticModel(),
        goal="编制基站风险报告",
        subgoal="查询厂商版本兼容矩阵",
        tool="knowledge_search",
        action="search",
        target="kb://release-matrix",
    )
    assert result.score < 0.6
    assert result.confidence <= 0.2
    assert not hasattr(result, "decision")


def test_empty_input_has_no_confidence():
    result = _score(LightweightSemanticModel(), goal="")
    assert result.score == 0.5
    assert result.confidence == 0


def test_trained_artifact_is_deterministic_and_roundtrips(tmp_path):
    artifact = train_linear_model(_rows(), epochs=60)
    assert train_linear_model(_rows(), epochs=60) == artifact
    path = _write(tmp_path, artifact)
    model = LightweightSemanticModel(path)
    assert model.version == "intent-linear-v1@" + artifact["sha256"][:12]
    aligned = model.score(
        **{k: v for k, v in _rows()[0].items() if k not in {"split", "label"}}
    )
    drift = model.score(
        **{k: v for k, v in _rows()[1].items() if k not in {"split", "label"}}
    )
    assert aligned.score < drift.score
    assert aligned.reason == "linear_soft_score"
    assert 0 < drift.confidence <= 0.75
    assert _score(model) == _score(LightweightSemanticModel(path))


@pytest.mark.parametrize("split", [None, "validation", "test", "runtime"])
def test_training_rejects_nontraining_examples(split):
    rows = _rows()
    rows[0]["split"] = split
    with pytest.raises(ValueError, match="train split"):
        train_linear_model(rows)


def test_training_requires_both_labels():
    with pytest.raises(ValueError, match="both aligned and drift"):
        train_linear_model([_rows()[0]])


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("schema_version", True),
        ("dimensions", 0),
        ("dimensions", True),
        ("dimensions", 65537),
        ("max_chars_per_field", 100000),
        ("weights", {"4096": 0.3}),
        ("weights", {"-1": 0.3}),
        ("weights", {"01": 0.3}),
        ("weights", {"1": True}),
        ("weights", {"1": "0.2"}),
        ("weights", {"1": float("nan")}),
        ("weights", []),
        ("bias", float("inf")),
        ("encoder_version", "unknown"),
        ("training", {"split": "test", "examples": 4}),
    ],
)
def test_loader_rejects_invalid_artifact_even_with_updated_digest(tmp_path, field, bad):
    artifact = train_linear_model(_rows())
    artifact[field] = bad
    with pytest.raises(SemanticModelError):
        LightweightSemanticModel(_write(tmp_path, _resign(artifact)))


def test_loader_rejects_tampering_and_missing_files(tmp_path):
    artifact = train_linear_model(_rows())
    artifact["bias"] += 0.1
    with pytest.raises(SemanticModelError, match="digest mismatch"):
        LightweightSemanticModel(_write(tmp_path, artifact))
    with pytest.raises(SemanticModelError):
        LightweightSemanticModel(tmp_path / "missing.json")


def test_loader_bounds_file_size(tmp_path):
    path = tmp_path / "large.json"
    path.write_bytes(b" " * 2_000_001)
    with pytest.raises(SemanticModelError, match="byte limit"):
        LightweightSemanticModel(path)


def test_loader_rejects_duplicate_json_keys(tmp_path):
    artifact = train_linear_model(_rows())
    path = _write(tmp_path, artifact)
    serialized = path.read_text(encoding="utf-8")
    path.write_text(
        serialized.replace(
            '"schema_version": 1', '"schema_version": 2, "schema_version": 1'
        ),
        encoding="utf-8",
    )
    with pytest.raises(SemanticModelError, match="duplicate JSON key"):
        LightweightSemanticModel(path)


def test_input_and_cache_are_bounded_and_truncation_is_not_hidden():
    model = LightweightSemanticModel(max_chars_per_field=64, cache_size=2)
    short = _score(model, goal="设备" * 32)
    long = _score(model, goal="设备" * 20000)
    assert "input_truncated" not in short.reason
    assert "input_truncated" in long.reason
    assert long.confidence <= 0.1
    assert short.score == long.score
    for index in range(10):
        _score(model, target=f"设备-{index}")
        assert model.cache_entries <= 2
    uncached = LightweightSemanticModel(cache_size=0)
    _score(uncached)
    assert uncached.cache_entries == 0


@pytest.mark.parametrize(
    "option", [{"cache_size": -1}, {"cache_size": 2049}, {"max_chars_per_field": 0}]
)
def test_invalid_resource_limits_are_rejected(option):
    with pytest.raises(ValueError):
        LightweightSemanticModel(**option)
