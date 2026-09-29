import importlib.util
import json
from pathlib import Path

from ra_agent.events import BehaviorEvent

ROOT = Path(__file__).parents[2]


def test_generated_openapi_types_and_fixtures_are_current() -> None:
    spec = importlib.util.spec_from_file_location(
        "p2_exporter", ROOT / "scripts/export_core_event_contracts.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for path, content in module.artifacts().items():
        assert path.read_text(encoding="utf-8") == content, f"Regenerate {path}"


def test_fixture_has_plan_fields_and_all_four_extension_lifecycle_events() -> None:
    events = json.loads((ROOT / "tests/fixtures/core/behavior_events.json").read_text())
    models = [BehaviorEvent.model_validate(event) for event in events]
    assert [model.sequence for model in models] == [1, 2, 3, 4]
    assert [model.state for model in models] == [
        "WAITING_CONFIRMATION",
        "CONFIRMED",
        "ALLOW",
        "EXECUTED",
    ]
    assert all(
        model.object_digest is None and model.result_digest is None for model in models
    )
