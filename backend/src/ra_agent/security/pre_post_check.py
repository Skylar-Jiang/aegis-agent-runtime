"""Deterministic Phase 3 pre- and post-execution security checks."""

from __future__ import annotations

import hashlib
import ipaddress
from collections.abc import Iterable, Mapping
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Protocol, TypeGuard
from urllib.parse import unquote, urlparse

from ra_agent.contracts import (
    ExecutionStatus,
    PolicyDecision,
    PostCheckResult,
    PreCheckResult,
    RiskVerdict,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
    ToolSpec,
)

from .deep_checker import RuleBasedDeepSafetyChecker
from .permission_gate import RuleBasedPermissionGate
from .risk_classifier import RuleBasedRiskClassifier
from .rule_engine import RuleEngine


class PreExecutionChecker(Protocol):
    async def check(self, request: ToolCallRequest, verdict: RiskVerdict) -> PreCheckResult: ...


class PostExecutionChecker(Protocol):
    async def check(
        self, request: ToolCallRequest, execution: ToolExecutionResult
    ) -> PostCheckResult: ...


_PATH_ARGUMENT_KEYS = {
    "path",
    "paths",
    "source",
    "source_path",
    "destination",
    "destination_path",
    "target_path",
}
_MUTATING_TOOLS = {"write_file", "delete_file", "download_url", "memory_write"}
_FILE_TOOLS = {"list_dir", "read_file", "write_file", "delete_file"}
_DOWNLOAD_ARTIFACT_TYPES = {
    "download",
    "download_file",
    "quarantine_file",
    "quarantined_download",
}
_MEMORY_ARTIFACT_TYPES = {"memory", "memory_pending", "pending_memory"}
_READ_ONLY_MARKERS = (
    "read only",
    "do not modify",
    "without changing",
    "只读",
    "仅查看",
    "不要修改",
    "不做修改",
)
_OFFLINE_MARKERS = (
    "offline only",
    "do not use network",
    "without network",
    "离线完成",
    "不要联网",
    "不访问网络",
)
_NO_SHELL_MARKERS = (
    "do not run shell",
    "do not execute commands",
    "no shell",
    "不要执行命令",
    "禁止 shell",
    "禁止执行命令",
)
_MISSING = object()


class RuleBasedPreExecutionChecker:
    """Re-evaluate request safety before an executor may act.

    The upstream verdict is treated as untrusted input until it is correlated with
    the request and agrees with the deterministic classifier.
    """

    def __init__(self, rules: RuleEngine, tool_specs: Mapping[str, ToolSpec]) -> None:
        self.rules = rules
        self.tool_specs = dict(tool_specs)
        self._classifier = RuleBasedRiskClassifier(rules, self.tool_specs)
        self._permission_gate = RuleBasedPermissionGate(rules)

    async def check(self, request: ToolCallRequest, verdict: RiskVerdict) -> PreCheckResult:
        if not self.rules.valid:
            return self._failed(
                request,
                ["configuration_invalid"],
                f"Security configuration is invalid: {self.rules.error}",
            )

        signals: list[str] = []
        if verdict.request_id != request.request_id:
            signals.append("risk_verdict_correlation_mismatch")

        spec = self.tool_specs.get(request.tool_name)
        if spec is None:
            signals.append("unknown_tool")
        elif spec.name != request.tool_name:
            signals.append("tool_spec_mismatch")

        self._check_required_arguments(request, signals)
        self._check_paths(request, signals)
        self._check_objective_consistency(request, spec, signals)
        self._check_url_shape(request, signals)

        expected = await self._classifier.classify(request)
        if (
            verdict.risk_level is not expected.risk_level
            or verdict.recommended_decision is not expected.recommended_decision
            or not set(expected.signals).issubset(verdict.signals)
            or not set(expected.matched_rules).issubset(verdict.matched_rules)
            or verdict.requires_deep_check is not expected.requires_deep_check
            or verdict.requires_checkpoint is not expected.requires_checkpoint
        ):
            signals.append("risk_verdict_mismatch")

        if expected.recommended_decision is PolicyDecision.BLOCK:
            signals.append("risk_policy_block")
        if "indirect_injection" in expected.signals:
            signals.append("indirect_prompt_injection")
        if "memory_poisoning" in expected.signals:
            signals.append("memory_poisoning")
        if "sensitive_path" in expected.signals and request.source_type in {
            SourceType.EXTERNAL_DOCUMENT,
            SourceType.TOOL_OUTPUT,
        }:
            signals.append("untrusted_sensitive_access")

        if spec is not None:
            permission_result = await self._permission_gate.check(request, spec)
            if permission_result.request_id != request.request_id or any(
                decision.request_id != request.request_id
                for decision in permission_result.decisions
            ):
                signals.append("permission_correlation_mismatch")
            if not permission_result.allowed:
                signals.append("permission_denied")

        signals = _unique(signals)
        if signals:
            return self._failed(request, signals)
        return PreCheckResult(
            request_id=request.request_id,
            passed=True,
            reason="Request and risk verdict passed deterministic pre-execution checks",
            signals=[],
        )

    @staticmethod
    def _check_required_arguments(request: ToolCallRequest, signals: list[str]) -> None:
        arguments = request.arguments
        if request.tool_name in _FILE_TOOLS:
            _require_text(arguments, "path", "argument_path_invalid", signals)
        if request.tool_name == "write_file":
            if not isinstance(arguments.get("content"), str):
                signals.append("argument_content_invalid")
        elif request.tool_name == "run_shell":
            _require_text(arguments, "command", "argument_command_invalid", signals)
        elif request.tool_name == "download_url":
            _require_text(arguments, "url", "argument_url_invalid", signals)
            if (
                _first_text(
                    arguments,
                    ("destination", "destination_path", "path", "target_path"),
                )
                is None
            ):
                signals.append("argument_destination_invalid")
        elif request.tool_name in {"memory_read", "memory_write"}:
            _require_text(arguments, "key", "argument_memory_key_invalid", signals)
            if request.tool_name == "memory_write":
                memory_value = arguments.get("value", arguments.get("content", _MISSING))
                if memory_value is _MISSING:
                    signals.append("argument_memory_value_missing")
                elif memory_value is None:
                    signals.append("argument_memory_value_invalid")

    @staticmethod
    def _check_paths(request: ToolCallRequest, signals: list[str]) -> None:
        for key, value in request.arguments.items():
            if key not in _PATH_ARGUMENT_KEYS:
                continue
            if not isinstance(value, str) and not (
                isinstance(value, list) and all(isinstance(item, str) for item in value)
            ):
                signals.append("path_argument_type_invalid")
        for path in _path_arguments(request.arguments):
            if not path or path.isspace():
                signals.append("path_empty")
                continue
            if _has_path_traversal(path):
                signals.append("path_traversal")
            if _is_absolute_path(path):
                signals.append("absolute_path")

    @staticmethod
    def _check_objective_consistency(
        request: ToolCallRequest,
        spec: ToolSpec | None,
        signals: list[str],
    ) -> None:
        context = f"{request.objective}\n{request.context_summary}".casefold()
        if (
            spec is not None
            and spec.side_effect_type not in {"NONE", "READ"}
            and any(marker in context for marker in _READ_ONLY_MARKERS)
        ):
            signals.append("objective_side_effect_conflict")
        if (
            spec is not None
            and spec.network_required
            and any(marker in context for marker in _OFFLINE_MARKERS)
        ):
            signals.append("objective_network_conflict")
        if request.tool_name == "run_shell" and any(
            marker in context for marker in _NO_SHELL_MARKERS
        ):
            signals.append("objective_shell_conflict")

    @staticmethod
    def _check_url_shape(request: ToolCallRequest, signals: list[str]) -> None:
        if request.tool_name != "download_url":
            return
        target = request.arguments.get("url")
        if not isinstance(target, str) or not target.strip():
            return
        parsed = urlparse(target.strip())
        if parsed.username is not None or parsed.password is not None:
            signals.append("url_userinfo_forbidden")

    @staticmethod
    def _failed(
        request: ToolCallRequest,
        signals: list[str],
        reason: str | None = None,
    ) -> PreCheckResult:
        return PreCheckResult(
            request_id=request.request_id,
            passed=False,
            reason=reason or "Pre-execution safety check rejected request: " + ", ".join(signals),
            signals=signals,
        )


class RuleBasedPostExecutionChecker:
    """Validate execution output and all untrusted pending artifacts before commit.

    Download artifacts are expected to identify a quarantined payload with type,
    request_id, status, path, SHA-256, size, content_type, source_url and final_url.
    Pending-memory artifacts identify type, request_id, status, key and value/content.
    Additional fields are allowed so member C can include audit metadata.
    """

    def __init__(
        self,
        rules: RuleEngine,
        workspace_root: Path,
        pending_root: Path | None = None,
        quarantine_root: Path | None = None,
    ) -> None:
        self.rules = rules
        self.workspace_root = workspace_root.resolve(strict=False)
        self.pending_root = pending_root.resolve(strict=False) if pending_root is not None else None
        self.quarantine_root = (
            quarantine_root.resolve(strict=False) if quarantine_root is not None else None
        )
        self._deep_checker = RuleBasedDeepSafetyChecker(
            rules,
            self.workspace_root,
            self.pending_root,
        )

    async def check(
        self, request: ToolCallRequest, execution: ToolExecutionResult
    ) -> PostCheckResult:
        if not self.rules.valid:
            return self._failed(
                request,
                ["configuration_invalid"],
                f"Security configuration is invalid: {self.rules.error}",
            )

        signals: list[str] = []
        if (
            execution.request_id != request.request_id
            or execution.task_id != request.task_id
            or execution.step_id != request.step_id
        ):
            signals.append("correlation_mismatch")
        if execution.error is not None or execution.error_code is not None:
            signals.append("execution_reported_error")

        expected_status = (
            ExecutionStatus.PENDING_COMMIT
            if request.tool_name in _MUTATING_TOOLS
            else ExecutionStatus.SUCCESS
        )
        if execution.status is not expected_status:
            signals.append("unexpected_execution_status")
        if expected_status is ExecutionStatus.PENDING_COMMIT and not execution.checkpoint_id:
            signals.append("checkpoint_missing")
        if request.tool_name in _MUTATING_TOOLS and not execution.pending_changes:
            signals.append("pending_changes_missing")
        if len(execution.pending_changes) > self.rules.max_pending_changes:
            signals.append("pending_change_limit_exceeded")
        if len(execution.artifacts) > self.rules.max_pending_changes:
            signals.append("artifact_limit_exceeded")

        if request.tool_name in {"write_file", "delete_file"}:
            deep_result = await self._deep_checker.check(request, execution)
            if deep_result.request_id != request.request_id:
                signals.append("deep_check_correlation_mismatch")
            if not deep_result.passed:
                signals.extend(deep_result.signals or ["pending_file_rejected"])
        elif request.tool_name == "download_url":
            self._check_download(request, execution, signals)
        elif request.tool_name == "memory_write":
            self._check_pending_memory(request, execution, signals)
        elif self.rules.permissions_for(request.tool_name) is None:
            signals.append("unknown_tool")

        scan_text = "\n".join(
            _string_values([execution.output, execution.artifacts, execution.pending_changes])
        )
        if len(scan_text) > self.rules.max_output_characters:
            signals.append("output_scan_limit_exceeded")
        if self.rules.contains_secret(scan_text):
            signals.append("secret_exposure")
        if self.rules.match_signal_text("indirect_injection", scan_text):
            signals.append("indirect_injection_output")

        if (
            execution.started_at is not None
            and execution.finished_at is not None
            and execution.finished_at < execution.started_at
        ):
            signals.append("execution_time_invalid")

        signals = _unique(signals)
        if signals:
            return self._failed(request, signals)
        return PostCheckResult(
            request_id=request.request_id,
            passed=True,
            reason="Execution output and pending artifacts passed deterministic post-checks",
            signals=[],
        )

    def _check_download(
        self,
        request: ToolCallRequest,
        execution: ToolExecutionResult,
        signals: list[str],
    ) -> None:
        artifacts = [
            artifact
            for artifact in execution.artifacts
            if isinstance(artifact, Mapping)
            and _artifact_type(artifact) in _DOWNLOAD_ARTIFACT_TYPES
        ]
        if len(artifacts) != 1:
            signals.append("download_artifact_count_invalid")
            return
        artifact = artifacts[0]
        self._check_artifact_request(artifact, request.request_id, signals)
        if artifact.get("status") not in {"PENDING", "QUARANTINED"}:
            signals.append("download_status_invalid")

        pending_path = _first_text(artifact, ("quarantine_path", "path"))
        content_sha256 = _first_text(artifact, ("sha256", "content_sha256"))
        size_bytes = artifact.get("size_bytes")
        content_type = artifact.get("content_type")
        if pending_path is None:
            signals.append("quarantine_path_missing")
        elif not _is_normalized_relative_path(pending_path):
            signals.append("quarantine_path_not_normalized")
        elif PurePosixPath(pending_path).parts[0] != request.request_id:
            signals.append("quarantine_namespace_mismatch")
        if content_sha256 is None or not _is_sha256(content_sha256):
            signals.append("download_sha256_invalid")
        if not _is_nonnegative_int(size_bytes):
            signals.append("download_size_invalid")
        elif size_bytes > self.rules.max_download_bytes:
            signals.append("download_size_limit_exceeded")
        if not isinstance(content_type, str) or not content_type.strip():
            signals.append("download_content_type_missing")
        else:
            media_type = content_type.split(";", 1)[0].strip().casefold()
            if not any(
                fnmatchcase(media_type, pattern)
                for pattern in self.rules.allowed_download_content_types
            ):
                signals.append("download_content_type_blocked")

        request_url = _first_text(request.arguments, ("url",))
        source_url = _first_text(artifact, ("source_url", "url"))
        final_url = _first_text(artifact, ("final_url",))
        if source_url is None:
            signals.append("download_source_url_missing")
        elif request_url is not None and source_url != request_url:
            signals.append("download_source_url_mismatch")
        if final_url is None:
            signals.append("download_final_url_missing")

        urls = [url for url in (source_url, final_url) if url is not None]
        redirect_chain = artifact.get("redirect_chain", [])
        if not isinstance(redirect_chain, list) or any(
            not isinstance(url, str) or not url.strip() for url in redirect_chain
        ):
            signals.append("download_redirect_chain_invalid")
        else:
            urls.extend(url.strip() for url in redirect_chain)
        for url in urls:
            network_signal = _network_signal(self.rules, url)
            if network_signal is not None:
                signals.append(f"download_{network_signal}")
            parsed = urlparse(url)
            if parsed.username is not None or parsed.password is not None:
                signals.append("download_url_userinfo_forbidden")

        expected_target = _first_text(
            request.arguments,
            ("destination", "destination_path", "path", "target_path"),
        )
        artifact_target = _first_text(artifact, ("target_path", "destination"))
        if artifact_target is not None and artifact_target != expected_target:
            signals.append("download_target_mismatch")
        self._check_download_pending_changes(
            execution,
            pending_path,
            expected_target,
            signals,
        )

        if (
            pending_path is not None
            and content_sha256 is not None
            and _is_sha256(content_sha256)
            and _is_nonnegative_int(size_bytes)
        ):
            payload = self._read_verified_payload(
                self.quarantine_root,
                pending_path,
                content_sha256,
                size_bytes,
                signals,
            )
            if payload is not None:
                decoded = payload.decode("utf-8", errors="ignore")
                if self.rules.match_signal_text("indirect_injection", decoded):
                    signals.append("download_contains_prompt_injection")
                if self.rules.contains_secret(decoded):
                    signals.append("download_contains_secret")

    def _check_pending_memory(
        self,
        request: ToolCallRequest,
        execution: ToolExecutionResult,
        signals: list[str],
    ) -> None:
        artifacts = [
            artifact
            for artifact in execution.artifacts
            if isinstance(artifact, Mapping) and _artifact_type(artifact) in _MEMORY_ARTIFACT_TYPES
        ]
        if len(artifacts) != 1:
            signals.append("memory_artifact_count_invalid")
            return
        artifact = artifacts[0]
        self._check_artifact_request(artifact, request.request_id, signals)
        if artifact.get("status") != "PENDING":
            signals.append("memory_status_invalid")

        key = artifact.get("key")
        request_key = request.arguments.get("key")
        if not isinstance(key, str) or not key.strip():
            signals.append("memory_key_invalid")
        elif key != request_key:
            signals.append("memory_key_mismatch")

        value = artifact.get("value", artifact.get("content", _MISSING))
        request_value = request.arguments.get("value", request.arguments.get("content", _MISSING))
        if value is _MISSING:
            signals.append("memory_value_missing")
            return
        if request_value is _MISSING or value != request_value:
            signals.append("memory_value_mismatch")

        memory_text = "\n".join(_string_values([key, value]))
        if len(memory_text) > self.rules.max_memory_characters:
            signals.append("memory_size_limit_exceeded")
        if self.rules.match_signal_text(
            "indirect_injection", memory_text
        ) or self.rules.match_signal_text("memory_poisoning", memory_text):
            signals.append("memory_poisoning")
        if self.rules.contains_secret(memory_text):
            signals.append("secret_in_pending_memory")
        for change in execution.pending_changes:
            if not isinstance(change, Mapping):
                signals.append("malformed_pending_change")
                continue
            operation = change.get("operation")
            if operation is not None and operation not in {
                "MEMORY_WRITE",
                "WRITE",
                "CREATE",
                "UPDATE",
            }:
                signals.append("memory_operation_invalid")
            change_status = change.get("status")
            if change_status is not None and change_status != "PENDING":
                signals.append("memory_pending_status_invalid")
            change_key = change.get("key")
            if change_key is not None and change_key != request_key:
                signals.append("memory_pending_key_mismatch")

    @staticmethod
    def _check_download_pending_changes(
        execution: ToolExecutionResult,
        pending_path: str | None,
        expected_target: str | None,
        signals: list[str],
    ) -> None:
        for change in execution.pending_changes:
            if not isinstance(change, Mapping):
                signals.append("malformed_pending_change")
                continue
            operation = change.get("operation")
            if operation is not None and operation not in {
                "DOWNLOAD",
                "WRITE",
                "CREATE",
            }:
                signals.append("download_operation_invalid")
            status = change.get("status")
            if status is not None and status != "PENDING":
                signals.append("download_pending_status_invalid")
            change_path = _first_text(change, ("quarantine_path", "path"))
            if change_path is not None and change_path != pending_path:
                signals.append("download_pending_path_mismatch")
            change_target = _first_text(change, ("target_path", "destination", "destination_path"))
            if change_target is not None and change_target != expected_target:
                signals.append("download_target_mismatch")

    @staticmethod
    def _check_artifact_request(
        artifact: Mapping[str, object],
        request_id: str,
        signals: list[str],
    ) -> None:
        if artifact.get("request_id") != request_id:
            signals.append("artifact_request_id_mismatch")

    def _read_verified_payload(
        self,
        root: Path | None,
        relative_path: str,
        expected_sha256: str,
        expected_size: int,
        signals: list[str],
    ) -> bytes | None:
        if root is None:
            signals.append("quarantine_root_unconfigured")
            return None
        resolved = _resolve_under_root(root, relative_path)
        if resolved is None:
            signals.append("quarantine_path_escape")
            return None
        try:
            if not resolved.is_file():
                signals.append("quarantine_payload_missing")
                return None
            digest = hashlib.sha256()
            payload = bytearray()
            with resolved.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    payload.extend(chunk)
                    if len(payload) > self.rules.max_download_bytes:
                        signals.append("download_size_limit_exceeded")
                        return None
        except OSError:
            signals.append("quarantine_payload_unreadable")
            return None
        if len(payload) != expected_size:
            signals.append("download_size_mismatch")
        if digest.hexdigest() != expected_sha256.casefold():
            signals.append("download_sha256_mismatch")
        return bytes(payload)

    @staticmethod
    def _failed(
        request: ToolCallRequest,
        signals: list[str],
        reason: str | None = None,
    ) -> PostCheckResult:
        return PostCheckResult(
            request_id=request.request_id,
            passed=False,
            reason=reason or "Post-execution safety check rejected result: " + ", ".join(signals),
            signals=signals,
        )


class MockPreExecutionChecker:
    async def check(self, request: ToolCallRequest, verdict: RiskVerdict) -> PreCheckResult:
        return PreCheckResult(request_id=request.request_id, passed=True, reason="mock")


class MockPostExecutionChecker:
    async def check(
        self, request: ToolCallRequest, execution: ToolExecutionResult
    ) -> PostCheckResult:
        return PostCheckResult(request_id=request.request_id, passed=True, reason="mock")


def _require_text(
    arguments: Mapping[str, object],
    key: str,
    signal: str,
    signals: list[str],
) -> None:
    value = arguments.get(key)
    if not isinstance(value, str) or not value.strip():
        signals.append(signal)


def _first_text(value: Mapping[str, object], keys: Iterable[str]) -> str | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item.strip():
            return item.strip()
    return None


def _path_arguments(arguments: Mapping[str, object]) -> Iterable[str]:
    for key, value in arguments.items():
        if key not in _PATH_ARGUMENT_KEYS:
            continue
        if isinstance(value, str):
            yield value
        elif isinstance(value, list):
            yield from (item for item in value if isinstance(item, str))


def _has_path_traversal(value: str) -> bool:
    decoded = unquote(unquote(value)).replace("\\", "/")
    return any(part == ".." for part in decoded.split("/"))


def _is_absolute_path(value: str) -> bool:
    return (
        Path(value).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or value.startswith(("/", "\\\\"))
    )


def _is_normalized_relative_path(value: str) -> bool:
    if not value or "\\" in value or _is_absolute_path(value):
        return False
    path = PurePosixPath(value)
    return all(part not in {"", ".", ".."} for part in path.parts) and path.as_posix() == value


def _resolve_under_root(root: Path, value: str) -> Path | None:
    if not _is_normalized_relative_path(value):
        return None
    resolved = (root / Path(value)).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def _network_signal(rules: RuleEngine, target: str) -> str | None:
    parsed = urlparse(target)
    scheme = parsed.scheme.casefold()
    host = (parsed.hostname or "").casefold().rstrip(".")
    if not scheme or not host or scheme not in rules.allowed_network_schemes:
        return "blocked_network"
    if any(fnmatchcase(host, pattern) for pattern in rules.blocked_network_hosts):
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
    if not any(fnmatchcase(host, pattern) for pattern in rules.allowed_network_hosts):
        return "unapproved_network"
    return None


def _artifact_type(artifact: Mapping[str, object]) -> str:
    value = artifact.get("type")
    return value.casefold() if isinstance(value, str) else ""


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdefABCDEF" for character in value)


def _is_nonnegative_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _string_values(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        result: list[str] = []
        for key, item in value.items():
            result.append(str(key))
            result.extend(_string_values(item))
        return result
    if isinstance(value, list | tuple | set):
        result = []
        for item in value:
            result.extend(_string_values(item))
        return result
    if value is None:
        return []
    return [str(value)]


def _unique(signals: list[str]) -> list[str]:
    return list(dict.fromkeys(signals))
