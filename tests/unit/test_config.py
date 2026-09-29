from pathlib import Path

import yaml


def test_all_yaml_configs_parse_and_share_frozen_risk_mapping() -> None:
    config_root = Path(__file__).parents[2] / "configs"
    configs = {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in config_root.glob("*.yaml")
    }

    assert set(configs) == {
        "permissions.yaml",
        "risk_rules.yaml",
        "runtime.yaml",
        "sensitive_paths.yaml",
        "tool_policies.yaml",
    }
    assert configs["risk_rules.yaml"]["decision_by_risk"] == {
        "LOW": "FAST_EXECUTE",
        "MEDIUM": "SANDBOX_CHECK",
        "HIGH": "REQUEST_APPROVAL",
        "CRITICAL": "BLOCK",
        "FORBIDDEN": "BLOCK",
    }
