from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import unquote

from ra_agent.contracts import (
    DeepCheckResult,
    ExecutionStatus,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution.pending_store import (
    PendingOperation,
    PendingRecord,
    PendingStatus,
    PendingStore,
    PendingStoreError,
)

from .rule_engine import RuleEngine


class DeepSafetyChecker(Protocol):
    async def check(
        self, request: ToolCallRequest, result: ToolExecutionResult
    ) -> DeepCheckResult: ...


class RuleBasedDeepSafetyChecker:
    """Validates PendingRecord manifests and payloads before CommitGate."""

    _PENDING_RECORD_VERSION = 1
    _PENDING_RECORD_FIELDS = {
        "version",
        "request_id",
        "checkpoint_id",
        "tool_name",
        "operation",
        "target_path",
        "pending_path",
        "content_sha256",
        "size_bytes",
        "created_at",
        "status",
    }
    _STRICT_FILE_TOOLS = {"write_file", "delete_file"}
    _FILE_MUTATION_TOOLS = _STRICT_FILE_TOOLS | {"download_url"}
    _PENDING_TOOLS = _FILE_MUTATION_TOOLS | {"memory_write"}
    _EXPECTED_OPERATIONS = {
        "write_file": "WRITE",
        "delete_file": "DELETE",
    }
    _LEGACY_OPERATIONS = {
        "download_url": {"write", "download", "create"},
        "memory_write": {"write", "create", "update"},
    }
    _SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")

    def __init__(
        self,
        rules: RuleEngine,
        workspace_root: Path,
        pending_root: Path | None = None,
    ) -> None:
        self.rules = rules
        self.workspace_root = workspace_root.resolve(strict=False)
        self.pending_store = PendingStore(pending_root) if pending_root is not None else None
        self.pending_root = (
            self.pending_store.pending_root if self.pending_store is not None else None
        )

    async def check(self, request: ToolCallRequest, result: ToolExecutionResult) -> DeepCheckResult:
        signals: list[str] = []
        if not self.rules.valid:
            return self._failed(
                request,
                f"Security configuration is invalid: {self.rules.error}",
                ["configuration_invalid"],
            )
        if (
            result.request_id != request.request_id
            or result.task_id != request.task_id
            or result.step_id != request.step_id
        ):
            signals.append("correlation_mismatch")
        if result.status is not ExecutionStatus.PENDING_COMMIT:
            signals.append("unexpected_execution_status")
        if not result.checkpoint_id:
            signals.append("checkpoint_missing")
        if result.error is not None or result.error_code is not None:
            signals.append("execution_reported_error")

        pending_changes = result.pending_changes
        if request.tool_name in self._PENDING_TOOLS and not pending_changes:
            signals.append("pending_changes_missing")
        if len(pending_changes) > self.rules.max_pending_changes:
            signals.append("pending_change_limit_exceeded")
        if request.tool_name in self._FILE_MUTATION_TOOLS and len(pending_changes) > 1:
            signals.append("unexpected_side_effect_count")

        expected_path = self._expected_path(request)
        if request.tool_name in self._FILE_MUTATION_TOOLS and expected_path is None:
            signals.append("request_path_missing")
        shared_record: PendingRecord | None = None
        if request.tool_name in self._STRICT_FILE_TOOLS:
            shared_record = await self._load_shared_record(request, signals)
            if shared_record is not None:
                self._check_shared_record(
                    request,
                    result,
                    shared_record,
                    expected_path,
                    signals,
                )
        for change in pending_changes:
            if not isinstance(change, Mapping):
                signals.append("malformed_pending_change")
                continue
            if request.tool_name in self._STRICT_FILE_TOOLS:
                if "version" in change:
                    self._check_pending_record(request, result, change, expected_path, signals)
                    if shared_record is not None:
                        self._check_inline_manifest_summary(change, shared_record, signals)
                elif shared_record is not None:
                    self._check_pending_summary(change, shared_record, signals)
            else:
                self._check_legacy_pending_change(request.tool_name, change, expected_path, signals)

        for artifact in result.artifacts:
            if not isinstance(artifact, Mapping):
                signals.append("malformed_artifact")
                continue
            if artifact.get("type") == "pending_file":
                self._check_pending_artifact(request, artifact, shared_record, signals)
                continue
            artifact_path = artifact.get("target_path", artifact.get("path"))
            if isinstance(artifact_path, str) and artifact_path:
                self._check_target_path(artifact_path, expected_path, signals)

        output_text = "\n".join(
            self._string_values([result.output, result.artifacts, result.pending_changes])
        )
        if len(output_text) > self.rules.max_output_characters:
            signals.append("output_scan_limit_exceeded")
        if self.rules.contains_secret(output_text):
            signals.append("secret_exposure")

        signals = list(dict.fromkeys(signals))
        if signals:
            return self._failed(
                request,
                "Deep safety check rejected pending result: " + ", ".join(signals),
                signals,
            )
        return DeepCheckResult(
            request_id=request.request_id,
            passed=True,
            reason=("PendingRecord matches the request and passed deterministic safety rules"),
            signals=[],
        )

    async def _load_shared_record(
        self,
        request: ToolCallRequest,
        signals: list[str],
    ) -> PendingRecord | None:
        if self.pending_store is None:
            signals.append("pending_root_unconfigured")
            return None
        try:
            record = await self.pending_store.get(request.request_id)
            if not await self.pending_store.verify_integrity(request.request_id):
                signals.append("pending_record_integrity_failed")
            return record
        except (OSError, PendingStoreError, TypeError, ValueError):
            signals.append("pending_record_unavailable")
            return None

    def _check_shared_record(
        self,
        request: ToolCallRequest,
        result: ToolExecutionResult,
        record: PendingRecord,
        expected_path: str | None,
        signals: list[str],
    ) -> None:
        if record.request_id != request.request_id:
            signals.append("pending_request_id_mismatch")
        if record.tool_name != request.tool_name:
            signals.append("pending_tool_name_mismatch")
        if record.checkpoint_id is None:
            signals.append("pending_checkpoint_unbound")
        elif record.checkpoint_id != result.checkpoint_id:
            signals.append("pending_checkpoint_mismatch")

        expected_operation = PendingOperation(self._EXPECTED_OPERATIONS[request.tool_name])
        if record.operation is not expected_operation:
            signals.append("pending_operation_mismatch")
        self._check_target_path(record.target_path, expected_path, signals)
        if record.created_at.utcoffset() is None:
            signals.append("pending_created_at_not_aware")
        if record.status is not PendingStatus.PENDING:
            signals.append("pending_status_invalid")

        record_values = {
            "pending_path": record.pending_path,
            "content_sha256": record.content_sha256,
            "size_bytes": record.size_bytes,
        }
        if request.tool_name == "write_file":
            self._check_write_record(request, record_values, signals)
        else:
            self._check_delete_record(record_values, signals)

    def _check_pending_summary(
        self,
        summary: Mapping[str, object],
        record: PendingRecord,
        signals: list[str],
    ) -> None:
        expected_fields = {
            "operation",
            "target_path",
            "status",
        }
        if record.operation is PendingOperation.WRITE:
            expected_fields.update({"pending_path", "content_sha256", "size_bytes"})
        else:
            expected_fields.add("original_size_bytes")

        keys = set(summary)
        if expected_fields - keys:
            signals.append("pending_summary_missing_fields")
        if keys - expected_fields:
            signals.append("pending_summary_unexpected_fields")
        if summary.get("operation") != record.operation.value:
            signals.append("pending_operation_mismatch")
        if summary.get("target_path") != record.target_path:
            signals.append("pending_target_path_mismatch")
        if summary.get("status") != record.status.value:
            signals.append("pending_status_invalid")

        if record.operation is PendingOperation.WRITE:
            if summary.get("pending_path") != record.pending_path:
                signals.append("pending_payload_path_mismatch")
            if summary.get("content_sha256") != record.content_sha256:
                signals.append("pending_content_sha256_mismatch")
            if summary.get("size_bytes") != record.size_bytes:
                signals.append("pending_size_mismatch")
        else:
            original_size = summary.get("original_size_bytes")
            if (
                not isinstance(original_size, int)
                or isinstance(original_size, bool)
                or original_size < 0
            ):
                signals.append("pending_original_size_invalid")

    def _check_inline_manifest_summary(
        self,
        summary: Mapping[str, object],
        record: PendingRecord,
        signals: list[str],
    ) -> None:
        expected = {
            "version": PendingStore.MANIFEST_VERSION,
            "request_id": record.request_id,
            "checkpoint_id": record.checkpoint_id,
            "tool_name": record.tool_name,
            "operation": record.operation.value,
            "target_path": record.target_path,
            "pending_path": record.pending_path,
            "content_sha256": record.content_sha256,
            "size_bytes": record.size_bytes,
            "status": record.status.value,
        }
        mismatch = set(summary) != self._PENDING_RECORD_FIELDS or any(
            summary.get(field) != value for field, value in expected.items()
        )
        created_at = summary.get("created_at")
        if isinstance(created_at, str):
            try:
                parsed_created_at = datetime.fromisoformat(created_at)
            except ValueError:
                mismatch = True
            else:
                mismatch = (
                    parsed_created_at.utcoffset() is None
                    or parsed_created_at.astimezone(UTC) != record.created_at
                    or mismatch
                )
        else:
            mismatch = True
        if mismatch:
            signals.append("pending_record_store_mismatch")

    def _check_pending_artifact(
        self,
        request: ToolCallRequest,
        artifact: Mapping[str, object],
        record: PendingRecord | None,
        signals: list[str],
    ) -> None:
        expected_fields = {"type", "path", "sha256", "size_bytes"}
        keys = set(artifact)
        if expected_fields - keys:
            signals.append("pending_artifact_missing_fields")
        if keys - expected_fields:
            signals.append("pending_artifact_unexpected_fields")
        if request.tool_name != "write_file" or record is None:
            signals.append("unexpected_pending_artifact")
            return

        pending_path = artifact.get("path")
        if not isinstance(pending_path, str) or not pending_path:
            signals.append("pending_payload_path_missing")
        elif not self._is_normalized_relative_path(pending_path):
            signals.append("pending_payload_path_not_normalized")
        elif pending_path != record.pending_path:
            signals.append("pending_payload_path_mismatch")
        if artifact.get("sha256") != record.content_sha256:
            signals.append("pending_content_sha256_mismatch")
        if artifact.get("size_bytes") != record.size_bytes:
            signals.append("pending_size_mismatch")

    def _check_pending_record(
        self,
        request: ToolCallRequest,
        result: ToolExecutionResult,
        record: Mapping[str, object],
        expected_path: str | None,
        signals: list[str],
    ) -> None:
        keys = set(record)
        if self._PENDING_RECORD_FIELDS - keys:
            signals.append("pending_record_missing_fields")
        if keys - self._PENDING_RECORD_FIELDS:
            signals.append("pending_record_unexpected_fields")

        version = record.get("version")
        if (
            not isinstance(version, int)
            or isinstance(version, bool)
            or version != self._PENDING_RECORD_VERSION
        ):
            signals.append("pending_record_version_invalid")
        if record.get("request_id") != request.request_id:
            signals.append("pending_request_id_mismatch")
        if record.get("tool_name") != request.tool_name:
            signals.append("pending_tool_name_mismatch")

        record_checkpoint = record.get("checkpoint_id")
        if record_checkpoint is None:
            signals.append("pending_checkpoint_unbound")
        elif not isinstance(record_checkpoint, str):
            signals.append("pending_checkpoint_invalid")
        elif record_checkpoint != result.checkpoint_id:
            signals.append("pending_checkpoint_mismatch")

        expected_operation = self._EXPECTED_OPERATIONS[request.tool_name]
        if record.get("operation") != expected_operation:
            signals.append("pending_operation_mismatch")

        target_path = record.get("target_path")
        if not isinstance(target_path, str) or not target_path:
            signals.append("pending_target_path_missing")
        else:
            if not self._is_normalized_relative_path(target_path):
                signals.append("pending_target_path_not_normalized")
            self._check_target_path(target_path, expected_path, signals)

        self._check_created_at(record.get("created_at"), signals)
        if record.get("status") != "PENDING":
            signals.append("pending_status_invalid")

        if request.tool_name == "write_file":
            self._check_write_record(request, record, signals)
        else:
            self._check_delete_record(record, signals)

    def _check_write_record(
        self,
        request: ToolCallRequest,
        record: Mapping[str, object],
        signals: list[str],
    ) -> None:
        pending_path = record.get("pending_path")
        content_sha256 = record.get("content_sha256")
        size_bytes = record.get("size_bytes")

        if not isinstance(pending_path, str) or not pending_path:
            signals.append("pending_payload_path_missing")
        elif not self._is_normalized_relative_path(pending_path):
            signals.append("pending_payload_path_not_normalized")
        if (
            not isinstance(content_sha256, str)
            or self._SHA256_PATTERN.fullmatch(content_sha256) is None
        ):
            signals.append("pending_content_sha256_invalid")
        if not isinstance(size_bytes, int) or isinstance(size_bytes, bool) or size_bytes < 0:
            signals.append("pending_size_invalid")

        request_content = request.arguments.get("content")
        if not isinstance(request_content, str):
            signals.append("request_content_missing")
        elif isinstance(content_sha256, str) and isinstance(size_bytes, int):
            expected_bytes = request_content.encode("utf-8")
            if len(expected_bytes) != size_bytes:
                signals.append("request_content_size_mismatch")
            if hashlib.sha256(expected_bytes).hexdigest() != content_sha256.casefold():
                signals.append("request_content_sha256_mismatch")

        if (
            isinstance(pending_path, str)
            and pending_path
            and isinstance(content_sha256, str)
            and isinstance(size_bytes, int)
            and not isinstance(size_bytes, bool)
            and size_bytes >= 0
        ):
            self._check_pending_payload(
                request.request_id,
                pending_path,
                content_sha256,
                size_bytes,
                signals,
            )

    @staticmethod
    def _check_delete_record(record: Mapping[str, object], signals: list[str]) -> None:
        if record.get("pending_path") is not None:
            signals.append("delete_pending_path_must_be_null")
        if record.get("content_sha256") is not None:
            signals.append("delete_content_sha256_must_be_null")
        if record.get("size_bytes") is not None:
            signals.append("delete_size_must_be_null")

    def _check_pending_payload(
        self,
        request_id: str,
        pending_path: str,
        expected_sha256: str,
        expected_size: int,
        signals: list[str],
    ) -> None:
        parts = PurePosixPath(pending_path).parts
        if not parts or parts[0] != request_id:
            signals.append("pending_payload_namespace_mismatch")
        if self.pending_root is None:
            signals.append("pending_root_unconfigured")
            return

        resolved = self._resolve_under_root(self.pending_root, pending_path)
        if resolved is None:
            signals.append("pending_payload_escape")
            return
        try:
            if not resolved.is_file():
                signals.append("pending_payload_missing")
                return
            actual_size = resolved.stat().st_size
            digest = hashlib.sha256()
            with resolved.open("rb") as payload:
                for chunk in iter(lambda: payload.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            signals.append("pending_payload_unreadable")
            return
        if actual_size != expected_size:
            signals.append("pending_payload_size_mismatch")
        if digest.hexdigest() != expected_sha256.casefold():
            signals.append("pending_payload_sha256_mismatch")

    def _check_legacy_pending_change(
        self,
        tool_name: str,
        change: Mapping[str, object],
        expected_path: str | None,
        signals: list[str],
    ) -> None:
        operation = change.get("operation", change.get("action"))
        if not isinstance(operation, str):
            signals.append("malformed_pending_operation")
        else:
            expected = self._LEGACY_OPERATIONS.get(tool_name)
            if expected is not None and operation.casefold() not in expected:
                signals.append("side_effect_type_mismatch")
        if tool_name == "download_url":
            target_path = change.get("target_path", change.get("path"))
            if not isinstance(target_path, str) or not target_path:
                signals.append("pending_target_path_missing")
            else:
                self._check_target_path(target_path, expected_path, signals)

    def _check_target_path(
        self, actual_path: str, expected_path: str | None, signals: list[str]
    ) -> None:
        if self._has_path_traversal(actual_path):
            signals.append("path_traversal")
        resolved = self._resolve_workspace_path(actual_path)
        if resolved is None:
            signals.append("workspace_escape")
        if self.rules.matches_protected_path(actual_path):
            signals.append("protected_path_modified")
        elif self.rules.matches_sensitive_path(actual_path):
            signals.append("sensitive_path_modified")
        if expected_path is not None:
            expected = self._resolve_workspace_path(expected_path)
            if (
                resolved is None
                or expected is None
                or os.path.normcase(resolved) != os.path.normcase(expected)
            ):
                signals.append("request_result_path_mismatch")

    def _resolve_workspace_path(self, value: str) -> str | None:
        resolved = self._resolve_under_root(self.workspace_root, value)
        return str(resolved) if resolved is not None else None

    @staticmethod
    def _resolve_under_root(root: Path, value: str) -> Path | None:
        candidate = Path(value)
        if candidate.is_absolute():
            return None
        resolved = (root / candidate).resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            return None
        return resolved

    @staticmethod
    def _check_created_at(value: object, signals: list[str]) -> None:
        if not isinstance(value, str):
            signals.append("pending_created_at_invalid")
            return
        try:
            created_at = datetime.fromisoformat(value)
        except ValueError:
            signals.append("pending_created_at_invalid")
            return
        if created_at.utcoffset() is None:
            signals.append("pending_created_at_not_aware")

    @staticmethod
    def _is_normalized_relative_path(value: str) -> bool:
        if not value or "\\" in value or value.startswith("/"):
            return False
        path = PurePosixPath(value)
        return (
            not Path(value).is_absolute()
            and all(part not in {"", ".", ".."} for part in path.parts)
            and path.as_posix() == value
        )

    @staticmethod
    def _expected_path(request: ToolCallRequest) -> str | None:
        for key in ("path", "destination", "destination_path", "target_path"):
            value = request.arguments.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    @staticmethod
    def _has_path_traversal(path: str) -> bool:
        decoded = unquote(unquote(path)).replace("\\", "/")
        return any(part == ".." for part in decoded.split("/"))

    @classmethod
    def _string_values(cls, value: object) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, Mapping):
            result: list[str] = []
            for key, item in value.items():
                result.append(str(key))
                result.extend(cls._string_values(item))
            return result
        if isinstance(value, list | tuple | set):
            result = []
            for item in value:
                result.extend(cls._string_values(item))
            return result
        return []

    @staticmethod
    def _failed(request: ToolCallRequest, reason: str, signals: list[str]) -> DeepCheckResult:
        return DeepCheckResult(
            request_id=request.request_id,
            passed=False,
            reason=reason,
            signals=signals,
        )


class MockDeepSafetyChecker:
    """Phase 1 mock; it must never be presented as a real deep checker."""

    def __init__(self, *, passed: bool = True, reason: str = "mock deep-check result") -> None:
        self.passed = passed
        self.reason = reason

    async def check(self, request: ToolCallRequest, result: ToolExecutionResult) -> DeepCheckResult:
        return DeepCheckResult(
            request_id=request.request_id,
            passed=self.passed,
            reason=self.reason,
        )
