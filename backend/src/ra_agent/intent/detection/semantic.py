"""Bounded lexical baseline and optional trained, feature-hashed linear scorer.

Scores are soft signals, not calibrated probabilities or authorization decisions.
The built-in baseline is deliberately named lexical: it has no learned weights.
Training is explicit and offline; this module never learns from runtime input.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENCODER_VERSION = "joint-hash-v1"
BASELINE_VERSION = "lexical-baseline-v1"
FIELDS = ("goal", "subgoal", "tool", "action", "target", "parameters_text")
MAX_ARTIFACT_BYTES = 2_000_000
MAX_TRAINING_EXAMPLES = 10_000
_TOKENS = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]+")


class SemanticModelError(ValueError):
    """An explicitly requested artifact could not be loaded or validated."""


@dataclass(frozen=True)
class SemanticResult:
    score: float
    confidence: float
    reason: str
    model_version: str
    evidence_terms: tuple[str, ...] = ()


def _integer(value: object, minimum: int, maximum: int, name: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _number(value: object, name: str, bound: float = 100.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or abs(result) > bound:
        raise ValueError(f"{name} must be finite with absolute value <= {bound}")
    return result


def _terms(text: str) -> set[str]:
    terms: set[str] = set()
    for token in _TOKENS.findall(text.casefold()):
        if "\u3400" <= token[0] <= "\u9fff":
            # Character bigrams need no tokenizer download and retain Chinese phrases.
            terms.update(token[i : i + 2] for i in range(max(1, len(token) - 1)))
        else:
            terms.add(token)
    return terms


def _overlap(goal: set[str], candidate: set[str]) -> float:
    return len(goal & candidate) / max(1, len(goal))


def _features(values: tuple[str, ...], dimensions: int) -> dict[int, float]:
    tokens = [_terms(value) for value in values]
    features: dict[str, float] = {}
    for field, terms in zip(FIELDS, tokens, strict=True):
        norm = max(1.0, math.sqrt(len(terms)))
        for term in sorted(terms):
            features[f"{field}:{term}"] = 1.0 / norm
    candidate = set().union(*tokens[1:])
    shared = tokens[0] & candidate
    for term in sorted(shared):
        features[f"joint:{term}"] = 1.0 / max(1.0, math.sqrt(len(shared)))
    # Joint coverage and role interactions differ from independent bag-of-word fields.
    for field, terms in zip(FIELDS[1:], tokens[1:], strict=True):
        coverage = _overlap(tokens[0], terms)
        features[f"goal-{field}-coverage:{min(4, int(coverage * 4))}"] = 1.0
    for goal_term in sorted(tokens[0])[:24]:
        for action_term in sorted(tokens[2] | tokens[3])[:24]:
            features[f"goal-action:{goal_term}:{action_term}"] = 1.0 / 24.0
    hashed: dict[int, float] = {}
    for feature, value in features.items():
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        signed = value if digest[4] & 1 else -value
        hashed[index] = hashed.get(index, 0.0) + signed
    norm = math.sqrt(sum(value * value for value in hashed.values())) or 1.0
    return {index: value / norm for index, value in hashed.items()}


def _sigmoid(value: float) -> float:
    value = min(35.0, max(-35.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def _digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _input_values(example: Mapping[str, Any], limit: int) -> tuple[tuple[str, ...], bool]:
    values: list[str] = []
    truncated = False
    for field in FIELDS:
        value = example.get(field, "")
        if not isinstance(value, str):
            raise TypeError(f"{field} must be a string")
        truncated |= len(value) > limit
        values.append(value[:limit])
    return tuple(values), truncated


class LightweightSemanticModel:
    """Synchronous scorer with bounded input and LRU cache, independent of schemas.

    An invalid supplied artifact raises SemanticModelError; it never silently
    becomes a lexical result. The caller owns any detector-unavailable policy.
    """

    def __init__(
        self,
        artifact_path: str | Path | None = None,
        *,
        max_chars_per_field: int = 1024,
        cache_size: int = 128,
    ) -> None:
        self.max_chars_per_field = _integer(max_chars_per_field, 64, 4096, "max_chars_per_field")
        self.cache_size = _integer(cache_size, 0, 2048, "cache_size")
        self._cache: OrderedDict[tuple[tuple[str, ...], bool], SemanticResult] = OrderedDict()
        self._version = BASELINE_VERSION
        self._dimensions = 4096
        self._weights: dict[int, float] | None = None
        self._bias = 0.0
        self._examples = 0
        if artifact_path is not None:
            self._load(Path(artifact_path))

    @property
    def version(self) -> str:
        return self._version

    @property
    def cache_entries(self) -> int:
        return len(self._cache)

    def set_cache_limit(self, limit: int) -> None:
        """Reduce retained inputs immediately when an approved policy changes."""
        self.cache_size = _integer(limit, 0, 2048, "cache_size")
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)

    def _load(self, path: Path) -> None:
        try:
            with path.open("rb") as stream:
                raw = stream.read(MAX_ARTIFACT_BYTES + 1)
            if len(raw) > MAX_ARTIFACT_BYTES:
                raise ValueError("artifact exceeds byte limit")
            payload = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(payload, dict):
                raise ValueError("artifact must be an object")
            expected = {
                "schema_version",
                "encoder_version",
                "model_version",
                "dimensions",
                "max_chars_per_field",
                "weights",
                "bias",
                "training",
                "sha256",
            }
            if set(payload) != expected:
                raise ValueError("artifact fields do not match the supported schema")
            digest = payload.pop("sha256")
            if not isinstance(digest, str) or _digest(payload) != digest:
                raise ValueError("artifact digest mismatch")
            if (
                type(payload["schema_version"]) is not int
                or payload["schema_version"] != 1
                or payload["encoder_version"] != ENCODER_VERSION
            ):
                raise ValueError("unsupported artifact or encoder version")
            version = payload["model_version"]
            if not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", version):
                raise ValueError("invalid model version")
            dimensions = _integer(payload["dimensions"], 128, 65536, "dimensions")
            limit = _integer(payload["max_chars_per_field"], 64, 4096, "max_chars_per_field")
            weights = payload["weights"]
            if not isinstance(weights, dict) or not weights or len(weights) > dimensions:
                raise ValueError("weights must be a nonempty sparse object")
            parsed: dict[int, float] = {}
            for key, value in weights.items():
                if not isinstance(key, str) or not key.isascii() or not key.isdigit():
                    raise ValueError("weight index must be a decimal string")
                index = int(key)
                if str(index) != key or not 0 <= index < dimensions:
                    raise ValueError("weight index is outside the declared dimensions")
                parsed[index] = _number(value, "weight")
            training = payload["training"]
            if (
                not isinstance(training, dict)
                or set(training) != {"split", "examples", "epochs"}
                or training.get("split") != "train"
            ):
                raise ValueError("artifact must declare the train split")
            examples = _integer(training.get("examples"), 2, MAX_TRAINING_EXAMPLES, "examples")
            _integer(training["epochs"], 1, 200, "epochs")
            bias = _number(payload["bias"], "bias")
        except (OSError, ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise SemanticModelError(f"Cannot load semantic model: {exc}") from exc
        # The artifact fixes the encoder limit; runtime must not silently change it.
        self.max_chars_per_field = limit
        self._dimensions, self._weights, self._bias = dimensions, parsed, bias
        self._examples = examples
        self._version = f"{version}@{digest[:12]}"

    def score(
        self,
        *,
        goal: str,
        subgoal: str,
        tool: str,
        action: str,
        target: str,
        parameters_text: str = "",
    ) -> SemanticResult:
        values, truncated = _input_values(
            dict(zip(FIELDS, (goal, subgoal, tool, action, target, parameters_text), strict=True)),
            self.max_chars_per_field,
        )
        key = (values, truncated)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        goal_terms = _terms(values[0])
        candidate_terms = set().union(*(_terms(value) for value in values[1:]))
        shared = tuple(sorted(goal_terms & candidate_terms)[:8])
        if not goal_terms or not candidate_terms:
            result = SemanticResult(0.5, 0.0, "insufficient_text", self.version, shared)
        elif self._weights is None:
            score = 0.55 - 0.3 * _overlap(goal_terms, candidate_terms)
            result = SemanticResult(score, 0.15, "lexical_baseline_uncertain", self.version, shared)
        else:
            features = _features(values, self._dimensions)
            score = _sigmoid(
                self._bias + sum(self._weights.get(i, 0.0) * value for i, value in features.items())
            )
            # This is evidence strength, not measured accuracy or calibrated probability.
            support = min(1.0, self._examples / 100.0)
            confidence = min(0.75, 0.15 + 0.6 * support * abs(2.0 * score - 1.0))
            result = SemanticResult(score, confidence, "linear_soft_score", self.version, shared)
        if truncated:
            result = SemanticResult(
                result.score,
                min(0.1, result.confidence),
                result.reason + ":input_truncated",
                result.model_version,
                result.evidence_terms,
            )
        if self.cache_size:
            self._cache[key] = result
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return result


def train_linear_model(
    examples: Iterable[Mapping[str, Any]],
    *,
    epochs: int = 30,
    dimensions: int = 4096,
    learning_rate: float = 0.5,
    max_chars_per_field: int = 1024,
    model_version: str = "intent-linear-v1",
) -> dict[str, Any]:
    """Return a JSON artifact from explicit train rows; label 1 denotes drift.

    Each row requires split='train', label in {0,1}, goal and subgoal; tool,
    action, target and parameters_text default to empty strings. Both labels
    must be represented. Validation/test rows are rejected, never filtered in.
    This utility demonstrates an offline trainable model, not benchmark quality.
    """
    epochs = _integer(epochs, 1, 200, "epochs")
    dimensions = _integer(dimensions, 128, 65536, "dimensions")
    limit = _integer(max_chars_per_field, 64, 4096, "max_chars_per_field")
    rate = _number(learning_rate, "learning_rate", 5.0)
    if rate <= 0:
        raise ValueError("learning_rate must be positive")
    if not isinstance(model_version, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]{1,100}", model_version
    ):
        raise ValueError("invalid model version")
    rows: list[tuple[dict[int, float], int]] = []
    labels: set[int] = set()
    for example in examples:
        if len(rows) >= MAX_TRAINING_EXAMPLES:
            raise ValueError("training example limit exceeded")
        if not isinstance(example, Mapping) or example.get("split") != "train":
            raise ValueError("every example must explicitly belong to the train split")
        label = example.get("label")
        if type(label) is not int or label not in (0, 1):
            raise ValueError("label must be integer 0 or 1")
        values, _ = _input_values(example, limit)
        if not values[0].strip() or not values[1].strip():
            raise ValueError("goal and subgoal must be nonempty")
        rows.append((_features(values, dimensions), label))
        labels.add(label)
    if labels != {0, 1}:
        raise ValueError("training requires both aligned and drift examples")
    weights: dict[int, float] = {}
    bias = 0.0
    for epoch in range(epochs):
        step = rate / math.sqrt(1.0 + epoch)
        for features, label in rows:
            error = (
                _sigmoid(bias + sum(weights.get(i, 0.0) * x for i, x in features.items())) - label
            )
            for index, value in features.items():
                weights[index] = weights.get(index, 0.0) - step * error * value
            bias -= step * error
    payload: dict[str, Any] = {
        "schema_version": 1,
        "encoder_version": ENCODER_VERSION,
        "model_version": model_version,
        "dimensions": dimensions,
        "max_chars_per_field": limit,
        "weights": {str(index): round(value, 12) for index, value in sorted(weights.items())},
        "bias": round(bias, 12),
        "training": {"split": "train", "examples": len(rows), "epochs": epochs},
    }
    return {**payload, "sha256": _digest(payload)}
