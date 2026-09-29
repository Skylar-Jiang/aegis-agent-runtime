from pathlib import Path

import pytest

Settings = pytest.importorskip("ra_agent.core.config").Settings


def test_settings_have_safe_local_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.app_env == "development"
    assert settings.workspace_root == Path(".runtime/workspace")
    assert settings.llm_api_key == ""
    assert settings.max_agent_turns == 8
