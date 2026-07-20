from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Mapping
from fnmatch import fnmatchcase
from typing import Protocol
from urllib.parse import unquote, urlparse

from ra_agent.contracts import (
    PolicyDecision,
    RecoverabilityType,
    RiskLevel,
    RiskVerdict,
    SourceType,
    ToolCallRequest,
    ToolSpec,
)

from .rule_engine import RuleEngine


class RiskClassifier(Protocol):
    async def classify(self, request: ToolCallRequest) -> RiskVerdict: ...


_RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
    RiskLevel.FORBIDDEN: 4,
}


class RuleBasedRiskClassifier:
    """Deterministic risk classifier backed by trusted tool metadata and YAML rules."""

    def __init__(self, rules: RuleEngine, tool_specs: Mapping[str, ToolSpec]) -> None:
        self.rules = rules
        self.tool_specs = dict(tool_specs)

    async def classify(self, request: ToolCallRequest) -> RiskVerdict:
        if not self.rules.valid:
            return self._verdict(
                request,
                RiskLevel.FORBIDDEN,
                PolicyDecision.BLOCK,
                f"Security configuration is invalid: {self.rules.error}",
                signals=["configuration_invalid"],
                matched_rules=["fail_closed.configuration_invalid"],
                requires_deep_check=True,
                requires_checkpoint=True,
            )

        spec = self.tool_specs.get(request.tool_name)
        risk = spec.base_risk if spec is not None else self.rules.default_risk
        signals: list[str] = []
        matched_rules: list[str] = []

        if spec is None:
            risk = self._add_signal("unknown_tool", risk, signals, matched_rules)

        paths = tuple(self._path_arguments(request.arguments))
        for path in paths:
            if self._has_path_traversal(path):
                risk = self._add_signal("path_traversal", risk, signals, matched_rules)
            if self.rules.matches_protected_path(path):
                risk = self._add_signal("policy_modification", risk, signals, matched_rules)
            elif self.rules.matches_sensitive_path(path):
                risk = self._add_signal("sensitive_path", risk, signals, matched_rules)

        command = str(request.arguments.get("command", ""))
        if request.tool_name == "run_shell" and self.rules.match_signal_text(
            "dangerous_shell", command
        ):
            risk = self._add_signal("dangerous_shell", risk, signals, matched_rules)

        if spec is not None and spec.network_required:
            target = self._network_target(request.arguments)
            if target is None:
                risk = self._add_signal("missing_network_target", risk, signals, matched_rules)
            else:
                network_signal = self._network_signal(target)
                if network_signal is not None:
                    risk = self._add_signal(network_signal, risk, signals, matched_rules)

        context = "\n".join(
            [request.objective, request.context_summary, *self._flatten_strings(request.arguments)]
        )
        if self.rules.match_signal_text("indirect_injection", context):
            risk = self._add_signal("indirect_injection", risk, signals, matched_rules)
        if request.tool_name == "memory_write" and (
            self.rules.match_signal_text("indirect_injection", context)
            or self.rules.match_signal_text("memory_poisoning", context)
        ):
            risk = self._add_signal("memory_poisoning", risk, signals, matched_rules)
        if request.source_type in {SourceType.EXTERNAL_DOCUMENT, SourceType.TOOL_OUTPUT} and (
            self.rules.match_signal_text("indirect_injection", context)
        ):
            risk = self._max_risk(risk, self.rules.risk_for("indirect_injection"))

        if spec is not None and (
            spec.reversibility is RecoverabilityType.NON_REVERSIBLE
            or spec.sandbox_mode.startswith("DISABLED")
        ):
            risk = self._max_risk(risk, RiskLevel.HIGH)

        decision = self.rules.decision_for(risk)
        requires_deep_check = _RISK_ORDER[risk] >= _RISK_ORDER[RiskLevel.MEDIUM]
        requires_checkpoint = requires_deep_check and (
            spec is None
            or spec.side_effect_type not in {"NONE", "READ"}
            or _RISK_ORDER[risk] >= _RISK_ORDER[RiskLevel.HIGH]
        )
        if signals:
            reason = f"Risk {risk.value} after matching signals: " + ", ".join(signals)
        else:
            reason = f"Trusted tool metadata produced base risk {risk.value}"
        return self._verdict(
            request,
            risk,
            decision,
            reason,
            signals=signals,
            matched_rules=matched_rules,
            requires_deep_check=requires_deep_check,
            requires_checkpoint=requires_checkpoint,
        )

    def _add_signal(
        self,
        signal: str,
        current: RiskLevel,
        signals: list[str],
        matched_rules: list[str],
    ) -> RiskLevel:
        if signal not in signals:
            signals.append(signal)
            matched_rules.append(f"risk.{signal}")
        return self._max_risk(current, self.rules.risk_for(signal))

    def _network_signal(self, target: str) -> str | None:
        parsed = urlparse(target)
        scheme = parsed.scheme.casefold()
        host = (parsed.hostname or "").casefold().rstrip(".")
        if not scheme or not host or scheme not in self.rules.allowed_network_schemes:
            return "blocked_network"
        if self._host_matches(host, self.rules.blocked_network_hosts):
            return "blocked_network"
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            return "blocked_network"
        if not self._host_matches(host, self.rules.allowed_network_hosts):
            return "unapproved_network"
        return None

    @staticmethod
    def _host_matches(host: str, patterns: tuple[str, ...]) -> bool:
        return any(fnmatchcase(host, pattern) for pattern in patterns)

    @staticmethod
    def _network_target(arguments: Mapping[str, object]) -> str | None:
        for key in ("url", "uri", "target_url", "endpoint"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _path_arguments(arguments: Mapping[str, object]) -> Iterable[str]:
        path_keys = {
            "path",
            "paths",
            "source",
            "source_path",
            "destination",
            "destination_path",
            "target_path",
        }
        for key, value in arguments.items():
            if key not in path_keys:
                continue
            if isinstance(value, str):
                yield value
            elif isinstance(value, list):
                yield from (item for item in value if isinstance(item, str))

    @staticmethod
    def _has_path_traversal(path: str) -> bool:
        decoded = unquote(unquote(path)).replace("\\", "/")
        return any(part == ".." for part in decoded.split("/"))

    @classmethod
    def _flatten_strings(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, Mapping):
            result: list[str] = []
            for key, item in value.items():
                result.append(str(key))
                result.extend(cls._flatten_strings(item))
            return result
        if isinstance(value, list | tuple | set):
            result = []
            for item in value:
                result.extend(cls._flatten_strings(item))
            return result
        return []

    @staticmethod
    def _max_risk(first: RiskLevel, second: RiskLevel) -> RiskLevel:
        return first if _RISK_ORDER[first] >= _RISK_ORDER[second] else second

    @staticmethod
    def _verdict(
        request: ToolCallRequest,
        risk_level: RiskLevel,
        decision: PolicyDecision,
        reason: str,
        *,
        signals: list[str] | None = None,
        matched_rules: list[str] | None = None,
        requires_deep_check: bool = False,
        requires_checkpoint: bool = False,
    ) -> RiskVerdict:
        return RiskVerdict(
            request_id=request.request_id,
            risk_level=risk_level,
            recommended_decision=decision,
            reason=reason,
            signals=signals or [],
            matched_rules=matched_rules or [],
            requires_deep_check=requires_deep_check,
            requires_checkpoint=requires_checkpoint,
        )


class MockRiskClassifier:
    """Deterministic fixture baseline; this is not a real security control."""

    async def classify(self, request: ToolCallRequest) -> RiskVerdict:
        command = str(request.arguments.get("command", "")).lower()
        path = str(request.arguments.get("path", "")).lower()

        if request.tool_name == "run_shell" and "rm -rf" in command:
            return self._verdict(
                request,
                RiskLevel.CRITICAL,
                PolicyDecision.BLOCK,
                "Mock baseline blocks destructive shell commands",
                signals=["dangerous_shell"],
                matched_rules=["fixture.dangerous_shell"],
            )
        if request.tool_name == "read_file" and path == ".env":
            return self._verdict(
                request,
                RiskLevel.HIGH,
                PolicyDecision.REQUEST_APPROVAL,
                "Mock baseline requires approval for sensitive reads",
                signals=["sensitive_read"],
                matched_rules=["fixture.sensitive_read"],
                requires_deep_check=True,
                requires_checkpoint=True,
            )
        if request.tool_name in {"write_file", "download_url", "memory_write"}:
            return self._verdict(
                request,
                RiskLevel.MEDIUM,
                PolicyDecision.SANDBOX_CHECK,
                "Mock baseline routes writes through sandbox policy",
                requires_deep_check=True,
                requires_checkpoint=True,
            )
        if request.tool_name in {"delete_file", "run_shell"}:
            return self._verdict(
                request,
                RiskLevel.HIGH,
                PolicyDecision.REQUEST_APPROVAL,
                "Mock baseline requires approval for high-risk tools",
                requires_deep_check=True,
                requires_checkpoint=True,
            )
        if request.tool_name in {"list_dir", "read_file", "memory_read"}:
            return self._verdict(
                request,
                RiskLevel.LOW,
                PolicyDecision.FAST_EXECUTE,
                "Mock baseline permits known read-only tools",
            )
        return self._verdict(
            request,
            RiskLevel.HIGH,
            PolicyDecision.REQUEST_APPROVAL,
            "Mock baseline uses a conservative default for unknown semantics",
            requires_deep_check=True,
        )

    @staticmethod
    def _verdict(
        request: ToolCallRequest,
        risk_level: RiskLevel,
        decision: PolicyDecision,
        reason: str,
        *,
        signals: list[str] | None = None,
        matched_rules: list[str] | None = None,
        requires_deep_check: bool = False,
        requires_checkpoint: bool = False,
    ) -> RiskVerdict:
        return RiskVerdict(
            request_id=request.request_id,
            risk_level=risk_level,
            recommended_decision=decision,
            reason=reason,
            signals=signals or [],
            matched_rules=matched_rules or [],
            requires_deep_check=requires_deep_check,
            requires_checkpoint=requires_checkpoint,
        )
