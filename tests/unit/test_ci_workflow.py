from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def test_pnpm_is_available_before_setup_node_enables_pnpm_cache() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["frontend"]["steps"]
    uses = [step.get("uses") for step in steps]

    pnpm_setup = uses.index("pnpm/action-setup@v4")
    node_setup = uses.index("actions/setup-node@v4")

    assert pnpm_setup < node_setup
    assert steps[pnpm_setup]["with"]["version"] == "10.12.4"


def test_ci_uses_pinned_node_24() -> None:
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    steps = workflow["jobs"]["frontend"]["steps"]
    node_setup = next(
        step for step in steps if step.get("uses") == "actions/setup-node@v4"
    )

    assert node_setup["with"]["node-version"] == "24.14.0"
