from pathlib import Path


def test_pnpm_is_available_before_setup_node_enables_pnpm_cache() -> None:
    workflow = (
        Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")

    pnpm_setup = workflow.index("pnpm/action-setup@v4")
    node_setup = workflow.index("actions/setup-node@v4", workflow.index("frontend:"))

    assert pnpm_setup < node_setup
    assert "version: 10.12.4" in workflow
