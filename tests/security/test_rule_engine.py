from pathlib import Path

import pytest

from ra_agent.contracts import (
    PermissionStatus,
    PermissionType,
    PolicyDecision,
    RiskLevel,
)
from ra_agent.security.rule_engine import RuleEngine, SecurityConfigurationError


def test_loads_complete_security_configuration(rules: RuleEngine) -> None:
    assert rules.decision_for(RiskLevel.LOW) is PolicyDecision.FAST_EXECUTE
    assert rules.decision_for(RiskLevel.MEDIUM) is PolicyDecision.SANDBOX_CHECK
    assert rules.decision_for(RiskLevel.HIGH) is PolicyDecision.REQUEST_APPROVAL
    assert rules.decision_for(RiskLevel.CRITICAL) is PolicyDecision.BLOCK
    assert rules.risk_for("path_traversal") is RiskLevel.CRITICAL
    assert rules.matches_sensitive_path("nested/.ENV")
    assert rules.matches_sensitive_path("keys/service.pem")
    assert rules.matches_protected_path("configs/risk_rules.yaml")
    assert rules.contains_secret("api_key=abcdefgh12345678")


def test_permission_configuration_is_complete(rules: RuleEngine) -> None:
    assert rules.permissions_for("download_url") == (
        PermissionType.NETWORK_DOWNLOAD,
        PermissionType.FILE_WRITE,
    )
    assert (
        rules.permission_status_for("list_dir", PermissionType.FILE_LIST)
        is PermissionStatus.GRANTED
    )
    assert (
        rules.permission_status_for("run_shell", PermissionType.SHELL_EXEC)
        is PermissionStatus.DENIED
    )
    assert rules.permission_requires_approval(PermissionType.FILE_DELETE)


def test_malformed_yaml_creates_fail_closed_engine(tmp_path: Path) -> None:
    risk = tmp_path / "risk_rules.yaml"
    permissions = tmp_path / "permissions.yaml"
    sensitive = tmp_path / "sensitive_paths.yaml"
    risk.write_text("version: [not-valid", encoding="utf-8")
    permissions.write_text("version: 1", encoding="utf-8")
    sensitive.write_text("version: 1", encoding="utf-8")

    engine = RuleEngine.from_files(risk, permissions, sensitive)

    assert not engine.valid
    assert engine.decision_for(RiskLevel.LOW) is PolicyDecision.BLOCK
    assert (
        engine.permission_status_for("list_dir", PermissionType.FILE_LIST)
        is PermissionStatus.DENIED
    )


def test_strict_loading_reports_configuration_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing.yaml"

    with pytest.raises(SecurityConfigurationError):
        RuleEngine.from_files(missing, missing, missing, strict=True)
