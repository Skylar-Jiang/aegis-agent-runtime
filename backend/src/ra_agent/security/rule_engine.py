from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

from ra_agent.contracts import (
    PermissionStatus,
    PermissionType,
    PolicyDecision,
    RiskLevel,
    SourceType,
)


class SecurityConfigurationError(ValueError):
    """Raised when a security configuration is incomplete or unsafe."""


@dataclass(frozen=True, slots=True)
class SignalRule:
    risk: RiskLevel
    patterns: tuple[str, ...] = ()


class RuleEngine:
    """Strict, fail-closed view over the three security YAML files."""

    def __init__(
        self,
        *,
        valid: bool,
        error: str | None,
        default_risk: RiskLevel,
        default_decision: PolicyDecision,
        tool_risk_floors: dict[str, RiskLevel],
        decisions: dict[RiskLevel, PolicyDecision],
        signals: dict[str, SignalRule],
        allowed_network_schemes: tuple[str, ...],
        allowed_network_hosts: tuple[str, ...],
        blocked_network_hosts: tuple[str, ...],
        protected_paths: tuple[str, ...],
        max_pending_changes: int,
        max_output_characters: int,
        secret_patterns: tuple[str, ...],
        max_download_bytes: int,
        max_memory_characters: int,
        allowed_download_content_types: tuple[str, ...],
        tool_permissions: dict[str, tuple[PermissionType, ...]],
        permission_statuses: dict[str, dict[PermissionType, PermissionStatus]],
        default_permission_status: PermissionStatus,
        approval_required: frozenset[PermissionType],
        adaptive_source_types: frozenset[SourceType],
        adaptive_side_effect_types: frozenset[str],
        adaptive_lineage_sensitivities: frozenset[str],
        delayed_authorization: bool,
        sensitive_patterns: tuple[str, ...],
        sensitive_case_sensitive: bool,
    ) -> None:
        self.valid = valid
        self.error = error
        self.default_risk = default_risk
        self.default_decision = default_decision
        self._tool_risk_floors = tool_risk_floors
        self._decisions = decisions
        self._signals = signals
        self.allowed_network_schemes = allowed_network_schemes
        self.allowed_network_hosts = allowed_network_hosts
        self.blocked_network_hosts = blocked_network_hosts
        self.protected_paths = protected_paths
        self.max_pending_changes = max_pending_changes
        self.max_output_characters = max_output_characters
        self.secret_patterns = secret_patterns
        self._compiled_secret_patterns = tuple(re.compile(pattern) for pattern in secret_patterns)
        self.max_download_bytes = max_download_bytes
        self.max_memory_characters = max_memory_characters
        self.allowed_download_content_types = allowed_download_content_types
        self._tool_permissions = tool_permissions
        self._permission_statuses = permission_statuses
        self.default_permission_status = default_permission_status
        self.approval_required = approval_required
        self.adaptive_source_types = adaptive_source_types
        self.adaptive_side_effect_types = adaptive_side_effect_types
        self.adaptive_lineage_sensitivities = adaptive_lineage_sensitivities
        self.delayed_authorization = delayed_authorization
        self.sensitive_patterns = sensitive_patterns
        self.sensitive_case_sensitive = sensitive_case_sensitive

    @classmethod
    def from_directory(cls, config_directory: Path, *, strict: bool = False) -> RuleEngine:
        return cls.from_files(
            config_directory / "risk_rules.yaml",
            config_directory / "permissions.yaml",
            config_directory / "sensitive_paths.yaml",
            strict=strict,
        )

    @classmethod
    def from_files(
        cls,
        risk_rules_path: Path,
        permissions_path: Path,
        sensitive_paths_path: Path,
        *,
        strict: bool = False,
    ) -> RuleEngine:
        try:
            risk_rules = cls._load_yaml(risk_rules_path)
            permissions = cls._load_yaml(permissions_path)
            sensitive_paths = cls._load_yaml(sensitive_paths_path)
            return cls._from_data(risk_rules, permissions, sensitive_paths)
        except (OSError, SecurityConfigurationError, yaml.YAMLError, re.error) as error:
            if strict:
                if isinstance(error, SecurityConfigurationError):
                    raise
                raise SecurityConfigurationError(str(error)) from error
            return cls.invalid(str(error) or type(error).__name__)

    @classmethod
    def invalid(cls, reason: str) -> RuleEngine:
        return cls(
            valid=False,
            error=reason,
            default_risk=RiskLevel.FORBIDDEN,
            default_decision=PolicyDecision.BLOCK,
            tool_risk_floors={},
            decisions={level: PolicyDecision.BLOCK for level in RiskLevel},
            signals={},
            allowed_network_schemes=(),
            allowed_network_hosts=(),
            blocked_network_hosts=(),
            protected_paths=(),
            max_pending_changes=0,
            max_output_characters=0,
            secret_patterns=(),
            max_download_bytes=0,
            max_memory_characters=0,
            allowed_download_content_types=(),
            tool_permissions={},
            permission_statuses={},
            default_permission_status=PermissionStatus.DENIED,
            approval_required=frozenset(),
            adaptive_source_types=frozenset(),
            adaptive_side_effect_types=frozenset(),
            adaptive_lineage_sensitivities=frozenset(),
            delayed_authorization=True,
            sensitive_patterns=(),
            sensitive_case_sensitive=False,
        )

    @classmethod
    def _from_data(
        cls,
        risk_rules: dict[str, Any],
        permissions: dict[str, Any],
        sensitive_paths: dict[str, Any],
    ) -> RuleEngine:
        cls._require_keys(
            risk_rules,
            {
                "version",
                "default_risk",
                "default_decision",
                "tool_risk_floors",
                "decision_by_risk",
                "signals",
                "network",
                "protected_paths",
                "deep_check",
                "post_check",
            },
            "risk_rules.yaml",
        )
        cls._require_keys(
            permissions,
            {
                "version",
                "default",
                "tool_permissions",
                "permission_statuses",
                "approval_required",
                "adaptive_approval",
                "delayed_authorization",
            },
            "permissions.yaml",
        )
        cls._require_keys(
            sensitive_paths,
            {"version", "case_sensitive", "patterns", "note"},
            "sensitive_paths.yaml",
        )
        for label, document in (
            ("risk_rules.yaml", risk_rules),
            ("permissions.yaml", permissions),
            ("sensitive_paths.yaml", sensitive_paths),
        ):
            if document.get("version") != 1:
                raise SecurityConfigurationError(f"{label}: version must be 1")

        default_risk = cls._enum(RiskLevel, risk_rules.get("default_risk"), "default_risk")
        default_decision = cls._enum(
            PolicyDecision, risk_rules.get("default_decision"), "default_decision"
        )
        floor_data = cls._mapping(risk_rules.get("tool_risk_floors"), "tool_risk_floors")
        tool_risk_floors = {
            str(tool): cls._enum(RiskLevel, risk, f"tool_risk_floors.{tool}")
            for tool, risk in floor_data.items()
            if isinstance(tool, str) and tool
        }
        if len(tool_risk_floors) != len(floor_data) or not tool_risk_floors:
            raise SecurityConfigurationError(
                "tool_risk_floors must be a non-empty mapping with string tool names"
            )

        decision_data = cls._mapping(risk_rules.get("decision_by_risk"), "decision_by_risk")
        decisions = {
            cls._enum(RiskLevel, risk, f"decision_by_risk.{risk}"): cls._enum(
                PolicyDecision, decision, f"decision_by_risk.{risk}"
            )
            for risk, decision in decision_data.items()
        }
        missing_risks = set(RiskLevel) - set(decisions)
        if missing_risks:
            names = ", ".join(sorted(item.value for item in missing_risks))
            raise SecurityConfigurationError(f"decision_by_risk is missing risk levels: {names}")

        signal_data = cls._mapping(risk_rules.get("signals"), "signals")
        signals: dict[str, SignalRule] = {}
        for name, raw_rule in signal_data.items():
            if not isinstance(name, str) or not name:
                raise SecurityConfigurationError("signal names must be non-empty strings")
            rule = cls._mapping(raw_rule, f"signals.{name}")
            cls._require_keys(rule, {"risk", "patterns", "examples"}, f"signals.{name}")
            patterns_value = rule.get("patterns", rule.get("examples", []))
            signals[name] = SignalRule(
                risk=cls._enum(RiskLevel, rule.get("risk"), f"signals.{name}.risk"),
                patterns=cls._string_list(patterns_value, f"signals.{name}.patterns"),
            )
        required_signals = {
            "dangerous_shell",
            "sensitive_path",
            "path_traversal",
            "unapproved_network",
            "blocked_network",
            "indirect_injection",
            "memory_poisoning",
            "policy_modification",
            "unknown_tool",
            "missing_network_target",
            "untrusted_data_flow",
            "sensitive_egress",
            "invalid_lineage",
            "unapproved_recipient",
            "secret_egress",
        }
        missing_signals = required_signals - set(signals)
        if missing_signals:
            raise SecurityConfigurationError(
                "signals is missing required rules: " + ", ".join(sorted(missing_signals))
            )

        network = cls._mapping(risk_rules.get("network"), "network")
        cls._require_keys(
            network,
            {"allowed_schemes", "allowed_hosts", "blocked_hosts"},
            "network",
        )
        allowed_network_schemes = tuple(
            item.casefold()
            for item in cls._string_list(network.get("allowed_schemes"), "network.allowed_schemes")
        )
        allowed_network_hosts = tuple(
            item.casefold()
            for item in cls._string_list(network.get("allowed_hosts"), "network.allowed_hosts")
        )
        blocked_network_hosts = tuple(
            item.casefold()
            for item in cls._string_list(network.get("blocked_hosts"), "network.blocked_hosts")
        )
        if not allowed_network_schemes or not allowed_network_hosts:
            raise SecurityConfigurationError(
                "network allowlists must contain at least one scheme and host"
            )

        protected_paths = cls._string_list(risk_rules.get("protected_paths"), "protected_paths")
        if not protected_paths:
            raise SecurityConfigurationError("protected_paths must not be empty")

        deep_check = cls._mapping(risk_rules.get("deep_check"), "deep_check")
        cls._require_keys(
            deep_check,
            {"max_pending_changes", "max_output_characters", "secret_patterns"},
            "deep_check",
        )
        max_pending_changes = cls._positive_int(
            deep_check.get("max_pending_changes"), "deep_check.max_pending_changes"
        )
        max_output_characters = cls._positive_int(
            deep_check.get("max_output_characters"), "deep_check.max_output_characters"
        )
        secret_patterns = cls._string_list(
            deep_check.get("secret_patterns"), "deep_check.secret_patterns"
        )
        if not secret_patterns:
            raise SecurityConfigurationError("deep_check.secret_patterns must not be empty")
        for pattern in secret_patterns:
            re.compile(pattern)

        post_check = cls._mapping(risk_rules.get("post_check"), "post_check")
        cls._require_keys(
            post_check,
            {
                "max_download_bytes",
                "max_memory_characters",
                "allowed_download_content_types",
            },
            "post_check",
        )
        max_download_bytes = cls._positive_int(
            post_check.get("max_download_bytes"), "post_check.max_download_bytes"
        )
        max_memory_characters = cls._positive_int(
            post_check.get("max_memory_characters"), "post_check.max_memory_characters"
        )
        allowed_download_content_types = tuple(
            item.casefold()
            for item in cls._string_list(
                post_check.get("allowed_download_content_types"),
                "post_check.allowed_download_content_types",
            )
        )
        if not allowed_download_content_types:
            raise SecurityConfigurationError(
                "post_check.allowed_download_content_types must not be empty"
            )

        tool_permission_data = cls._mapping(permissions.get("tool_permissions"), "tool_permissions")
        tool_permissions = {
            str(tool): tuple(
                cls._enum(PermissionType, permission, f"tool_permissions.{tool}")
                for permission in cls._string_list(raw_permissions, f"tool_permissions.{tool}")
            )
            for tool, raw_permissions in tool_permission_data.items()
        }
        if not tool_permissions:
            raise SecurityConfigurationError("tool_permissions must not be empty")

        status_data = cls._mapping(permissions.get("permission_statuses"), "permission_statuses")
        permission_statuses: dict[str, dict[PermissionType, PermissionStatus]] = {}
        for tool, raw_statuses in status_data.items():
            statuses = cls._mapping(raw_statuses, f"permission_statuses.{tool}")
            permission_statuses[str(tool)] = {
                cls._enum(
                    PermissionType,
                    permission,
                    f"permission_statuses.{tool}.{permission}",
                ): cls._enum(
                    PermissionStatus,
                    status,
                    f"permission_statuses.{tool}.{permission}",
                )
                for permission, status in statuses.items()
            }
        if set(permission_statuses) != set(tool_permissions):
            raise SecurityConfigurationError(
                "permission_statuses must contain exactly the tools in tool_permissions"
            )
        for tool, required in tool_permissions.items():
            if set(permission_statuses[tool]) != set(required):
                raise SecurityConfigurationError(
                    f"permission_statuses.{tool} must contain exactly its required permissions"
                )

        default_permission_status = cls._enum(
            PermissionStatus, permissions.get("default"), "default"
        )
        approval_required = frozenset(
            cls._enum(PermissionType, permission, "approval_required")
            for permission in cls._string_list(
                permissions.get("approval_required"), "approval_required"
            )
        )
        configured_permissions = {
            permission for required in tool_permissions.values() for permission in required
        }
        unknown_approval_permissions = approval_required - configured_permissions
        if unknown_approval_permissions:
            names = ", ".join(sorted(item.value for item in unknown_approval_permissions))
            raise SecurityConfigurationError(
                f"approval_required contains unused permissions: {names}"
            )

        adaptive_approval = cls._mapping(permissions.get("adaptive_approval"), "adaptive_approval")
        cls._require_keys(
            adaptive_approval,
            {
                "untrusted_source_types",
                "side_effect_types",
                "lineage_sensitivities",
            },
            "adaptive_approval",
        )
        adaptive_source_types = frozenset(
            cls._enum(SourceType, source, "adaptive_approval.untrusted_source_types")
            for source in cls._string_list(
                adaptive_approval.get("untrusted_source_types"),
                "adaptive_approval.untrusted_source_types",
            )
        )
        adaptive_side_effect_types = frozenset(
            cls._string_list(
                adaptive_approval.get("side_effect_types"),
                "adaptive_approval.side_effect_types",
            )
        )
        adaptive_lineage_sensitivities = frozenset(
            value.upper()
            for value in cls._string_list(
                adaptive_approval.get("lineage_sensitivities"),
                "adaptive_approval.lineage_sensitivities",
            )
        )
        if not adaptive_source_types or not adaptive_side_effect_types:
            raise SecurityConfigurationError(
                "adaptive_approval source and side-effect sets must not be empty"
            )
        allowed_sensitivities = {"PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"}
        if (
            not adaptive_lineage_sensitivities
            or not adaptive_lineage_sensitivities <= allowed_sensitivities
        ):
            raise SecurityConfigurationError(
                "adaptive_approval.lineage_sensitivities contains an unknown value"
            )
        delayed_authorization = permissions.get("delayed_authorization")
        if not isinstance(delayed_authorization, bool):
            raise SecurityConfigurationError("delayed_authorization must be a boolean")

        sensitive_case_sensitive = sensitive_paths.get("case_sensitive")
        if not isinstance(sensitive_case_sensitive, bool):
            raise SecurityConfigurationError("case_sensitive must be a boolean")
        sensitive_patterns = cls._string_list(
            sensitive_paths.get("patterns"), "sensitive_paths.patterns"
        )
        if not sensitive_patterns:
            raise SecurityConfigurationError("sensitive path patterns must not be empty")

        return cls(
            valid=True,
            error=None,
            default_risk=default_risk,
            default_decision=default_decision,
            tool_risk_floors=tool_risk_floors,
            decisions=decisions,
            signals=signals,
            allowed_network_schemes=allowed_network_schemes,
            allowed_network_hosts=allowed_network_hosts,
            blocked_network_hosts=blocked_network_hosts,
            protected_paths=protected_paths,
            max_pending_changes=max_pending_changes,
            max_output_characters=max_output_characters,
            secret_patterns=secret_patterns,
            max_download_bytes=max_download_bytes,
            max_memory_characters=max_memory_characters,
            allowed_download_content_types=allowed_download_content_types,
            tool_permissions=tool_permissions,
            permission_statuses=permission_statuses,
            default_permission_status=default_permission_status,
            approval_required=approval_required,
            adaptive_source_types=adaptive_source_types,
            adaptive_side_effect_types=adaptive_side_effect_types,
            adaptive_lineage_sensitivities=adaptive_lineage_sensitivities,
            delayed_authorization=delayed_authorization,
            sensitive_patterns=sensitive_patterns,
            sensitive_case_sensitive=sensitive_case_sensitive,
        )

    def decision_for(self, risk: RiskLevel) -> PolicyDecision:
        if not self.valid:
            return PolicyDecision.BLOCK
        return self._decisions.get(risk, PolicyDecision.BLOCK)

    def tool_risk_floor(self, tool_name: str) -> RiskLevel:
        if not self.valid:
            return RiskLevel.FORBIDDEN
        return self._tool_risk_floors.get(tool_name, self.default_risk)

    def risk_for(self, signal: str) -> RiskLevel:
        if not self.valid:
            return RiskLevel.FORBIDDEN
        rule = self._signals.get(signal)
        return rule.risk if rule is not None else self.default_risk

    def patterns_for(self, signal: str) -> tuple[str, ...]:
        rule = self._signals.get(signal)
        return rule.patterns if rule is not None else ()

    def match_signal_text(self, signal: str, text: str) -> bool:
        folded = text.casefold()
        return any(pattern.casefold() in folded for pattern in self.patterns_for(signal))

    def permissions_for(self, tool_name: str) -> tuple[PermissionType, ...] | None:
        return self._tool_permissions.get(tool_name)

    def permission_status_for(self, tool_name: str, permission: PermissionType) -> PermissionStatus:
        if not self.valid:
            return PermissionStatus.DENIED
        return self._permission_statuses.get(tool_name, {}).get(
            permission, self.default_permission_status
        )

    def permission_requires_approval(self, permission: PermissionType) -> bool:
        return permission in self.approval_required

    def matches_sensitive_path(self, value: str) -> bool:
        return self._matches_path_patterns(
            value,
            self.sensitive_patterns,
            case_sensitive=self.sensitive_case_sensitive,
        )

    def matches_protected_path(self, value: str) -> bool:
        return self._matches_path_patterns(value, self.protected_paths, case_sensitive=False)

    def contains_secret(self, value: str) -> bool:
        clipped = value[: self.max_output_characters]
        return any(pattern.search(clipped) for pattern in self._compiled_secret_patterns)

    @staticmethod
    def _matches_path_patterns(
        value: str, patterns: tuple[str, ...], *, case_sensitive: bool
    ) -> bool:
        decoded = unquote(unquote(value)).replace("\\", "/").strip("/")
        candidate = decoded if case_sensitive else decoded.casefold()
        parts = tuple(part for part in candidate.split("/") if part)
        basename = parts[-1] if parts else candidate
        for raw_pattern in patterns:
            normalized = raw_pattern.replace("\\", "/").strip("/")
            pattern = normalized if case_sensitive else normalized.casefold()
            if fnmatchcase(candidate, pattern) or fnmatchcase(basename, pattern):
                return True
            if "/" not in pattern and any(fnmatchcase(part, pattern) for part in parts):
                return True
        return False

    @staticmethod
    def _load_yaml(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            value = yaml.safe_load(handle)
        if not isinstance(value, dict):
            raise SecurityConfigurationError(f"{path.name}: root must be a mapping")
        return value

    @staticmethod
    def _mapping(value: object, label: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise SecurityConfigurationError(f"{label} must be a mapping")
        return value

    @staticmethod
    def _string_list(value: object, label: str) -> tuple[str, ...]:
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item for item in value
        ):
            raise SecurityConfigurationError(f"{label} must be a list of strings")
        return tuple(value)

    @staticmethod
    def _positive_int(value: object, label: str) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise SecurityConfigurationError(f"{label} must be a positive integer")
        return value

    @staticmethod
    def _enum(enum_type, value: object, label: str):
        if not isinstance(value, str):
            raise SecurityConfigurationError(f"{label} must be a string")
        try:
            return enum_type(value)
        except ValueError as error:
            raise SecurityConfigurationError(f"{label} has unknown value: {value}") from error

    @staticmethod
    def _require_keys(value: dict[str, Any], allowed: set[str], label: str) -> None:
        unknown = set(value) - allowed
        if unknown:
            raise SecurityConfigurationError(
                f"{label} contains unknown keys: " + ", ".join(sorted(unknown))
            )
