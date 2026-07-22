from __future__ import annotations

import json
import re
from collections.abc import Mapping
from hashlib import sha256
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from ra_agent.contracts import (
    ExecutionStatus,
    MemoryStatus,
    ToolCallRequest,
    ToolExecutionResult,
)

from .pending_store import PendingOperation, PendingRecord, PendingStatus

TOOL_OUTPUT_ARTIFACT = "tool_output"
PENDING_FILE_ARTIFACT = "pending_file"
PENDING_DELETE_ARTIFACT = "pending_delete"
QUARANTINED_DOWNLOAD_ARTIFACT = "quarantined_download"
PENDING_MEMORY_ARTIFACT = "pending_memory"

_ALLOWED_ARTIFACT_TYPES = frozenset(
    {
        TOOL_OUTPUT_ARTIFACT,
        PENDING_FILE_ARTIFACT,
        PENDING_DELETE_ARTIFACT,
        QUARANTINED_DOWNLOAD_ARTIFACT,
        PENDING_MEMORY_ARTIFACT,
    }
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ArtifactContractError(ValueError):
    """Raised when an internal execution artifact violates the stable schema."""


def canonical_json_bytes(value: object) -> bytes:
    """Serialize one tool output deterministically for hashing and inspection."""

    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ArtifactContractError("tool output must be finite JSON-serializable data") from error

    return serialized.encode("utf-8")


def build_tool_output_artifact(
    request: ToolCallRequest,
    output: object,
    *,
    status: ExecutionStatus,
) -> dict[str, Any]:
    """Build a digest that binds PostCheck to the exact handler output."""

    payload = canonical_json_bytes(output)
    artifact: dict[str, Any] = {
        **_base_fields(request),
        "artifact_type": TOOL_OUTPUT_ARTIFACT,
        "status": status.value,
        "content_type": "application/json",
        "encoding": "utf-8",
        "sha256": sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }
    validate_artifact(artifact)
    return artifact


def build_pending_file_artifact(
    request: ToolCallRequest,
    record: PendingRecord,
) -> dict[str, Any]:
    """Describe one staged write payload without exposing an absolute path."""

    _validate_pending_record_correlation(request, record)

    if record.operation is not PendingOperation.WRITE:
        raise ArtifactContractError("pending_file requires a WRITE pending record")

    if record.status is not PendingStatus.PENDING:
        raise ArtifactContractError("pending_file requires a PENDING record")

    if record.pending_path is None or record.content_sha256 is None or record.size_bytes is None:
        raise ArtifactContractError("pending write record is missing payload metadata")

    artifact: dict[str, Any] = {
        **_base_fields(request),
        "artifact_type": PENDING_FILE_ARTIFACT,
        "status": PendingStatus.PENDING.value,
        "path": record.pending_path,
        "target_path": record.target_path,
        "sha256": record.content_sha256,
        "size_bytes": record.size_bytes,
    }
    validate_artifact(artifact)
    return artifact


def build_pending_delete_artifact(
    request: ToolCallRequest,
    record: PendingRecord,
) -> dict[str, Any]:
    """Describe a staged delete marker with a deterministic marker digest."""

    _validate_pending_record_correlation(request, record)

    if record.operation is not PendingOperation.DELETE:
        raise ArtifactContractError("pending_delete requires a DELETE pending record")

    if record.status is not PendingStatus.PENDING:
        raise ArtifactContractError("pending_delete requires a PENDING record")

    marker = {
        "operation": record.operation.value,
        "target_path": record.target_path,
    }
    marker_payload = canonical_json_bytes(marker)
    artifact: dict[str, Any] = {
        **_base_fields(request),
        "artifact_type": PENDING_DELETE_ARTIFACT,
        "status": PendingStatus.PENDING.value,
        "target_path": record.target_path,
        "sha256": sha256(marker_payload).hexdigest(),
        "size_bytes": len(marker_payload),
    }
    validate_artifact(artifact)
    return artifact


def build_quarantined_download_artifact(
    request: ToolCallRequest,
    *,
    quarantine_path: str,
    source_url: str,
    final_url: str,
    content_sha256: str,
    size_bytes: int,
    content_type: str,
) -> dict[str, Any]:
    """Build the inspectable metadata boundary for a quarantined download."""

    if request.tool_name != "download_url":
        raise ArtifactContractError("quarantined download artifact requires download_url")

    artifact: dict[str, Any] = {
        **_base_fields(request),
        "artifact_type": QUARANTINED_DOWNLOAD_ARTIFACT,
        "status": "QUARANTINED",
        "quarantine_path": quarantine_path,
        "source_url": source_url,
        "final_url": final_url,
        "content_type": content_type,
        "sha256": content_sha256,
        "size_bytes": size_bytes,
    }
    validate_artifact(artifact)
    return artifact


def build_pending_memory_artifact(
    request: ToolCallRequest,
    *,
    memory_id: str,
    content_sha256: str,
    size_bytes: int,
) -> dict[str, Any]:
    """Build the inspectable metadata boundary for untrusted pending memory."""

    if request.tool_name != "memory_write":
        raise ArtifactContractError("pending memory artifact requires memory_write")

    artifact: dict[str, Any] = {
        **_base_fields(request),
        "artifact_type": PENDING_MEMORY_ARTIFACT,
        "status": MemoryStatus.PENDING.value,
        "memory_id": memory_id,
        "sha256": content_sha256,
        "size_bytes": size_bytes,
    }
    validate_artifact(artifact)
    return artifact


def validate_artifact(artifact: Mapping[str, Any]) -> None:
    """Validate one artifact independently of its enclosing execution result."""

    artifact_type = _require_string(artifact, "artifact_type")

    if artifact_type not in _ALLOWED_ARTIFACT_TYPES:
        raise ArtifactContractError(f"unsupported artifact_type: {artifact_type}")

    for field_name in ("task_id", "step_id", "request_id", "tool_name", "status"):
        _require_string(artifact, field_name)

    _require_sha256(artifact, "sha256")
    _require_size(artifact, "size_bytes")

    if artifact_type == TOOL_OUTPUT_ARTIFACT:
        _validate_tool_output_artifact(artifact)
    elif artifact_type == PENDING_FILE_ARTIFACT:
        _require_exact_status(artifact, PendingStatus.PENDING.value)
        _require_relative_path(artifact, "path")
        _require_relative_path(artifact, "target_path")
    elif artifact_type == PENDING_DELETE_ARTIFACT:
        _require_exact_status(artifact, PendingStatus.PENDING.value)
        _require_relative_path(artifact, "target_path")
    elif artifact_type == QUARANTINED_DOWNLOAD_ARTIFACT:
        _require_exact_status(artifact, "QUARANTINED")
        _require_relative_path(artifact, "quarantine_path")
        _require_string(artifact, "source_url")
        _require_string(artifact, "final_url")
        _require_string(artifact, "content_type")
    elif artifact_type == PENDING_MEMORY_ARTIFACT:
        _require_exact_status(artifact, MemoryStatus.PENDING.value)
        _require_string(artifact, "memory_id")


def validate_execution_artifacts(
    request: ToolCallRequest,
    execution: ToolExecutionResult,
) -> None:
    """Fail closed when handler artifacts are malformed, stale, or mis-correlated."""

    # Phase 3 keeps old orchestration mocks compatible. Real handlers emit artifacts;
    # once an artifact list is present, the entire list is validated strictly.
    if not execution.artifacts:
        return

    identities: set[tuple[str, str]] = set()
    artifact_types: set[str] = set()
    tool_output: Mapping[str, Any] | None = None

    for artifact in execution.artifacts:
        validate_artifact(artifact)

        for field_name, expected in (
            ("task_id", request.task_id),
            ("step_id", request.step_id),
            ("request_id", request.request_id),
            ("tool_name", request.tool_name),
        ):
            actual = _require_string(artifact, field_name)
            if actual != expected:
                raise ArtifactContractError(
                    f"artifact {field_name} does not match the tool request"
                )

        identity = _artifact_identity(artifact)
        if identity in identities:
            raise ArtifactContractError("execution contains a duplicate artifact identity")
        identities.add(identity)

        artifact_type = _require_string(artifact, "artifact_type")
        if artifact_type in artifact_types:
            raise ArtifactContractError("execution contains a duplicate artifact_type")
        artifact_types.add(artifact_type)

        if artifact_type == TOOL_OUTPUT_ARTIFACT:
            tool_output = artifact

    if tool_output is None:
        raise ArtifactContractError("artifact-bearing execution requires one tool_output artifact")

    expected_output = canonical_json_bytes(execution.output)
    if _require_size(tool_output, "size_bytes") != len(expected_output):
        raise ArtifactContractError("tool_output size does not match execution.output")

    if _require_sha256(tool_output, "sha256") != sha256(expected_output).hexdigest():
        raise ArtifactContractError("tool_output hash does not match execution.output")

    if _require_string(tool_output, "status") != execution.status.value:
        raise ArtifactContractError("tool_output status does not match execution status")

    required_types = {
        "list_dir": {TOOL_OUTPUT_ARTIFACT},
        "read_file": {TOOL_OUTPUT_ARTIFACT},
        "write_file": {TOOL_OUTPUT_ARTIFACT, PENDING_FILE_ARTIFACT},
        "delete_file": {TOOL_OUTPUT_ARTIFACT, PENDING_DELETE_ARTIFACT},
        "download_url": {TOOL_OUTPUT_ARTIFACT, QUARANTINED_DOWNLOAD_ARTIFACT},
        "memory_read": {TOOL_OUTPUT_ARTIFACT},
        "memory_write": {TOOL_OUTPUT_ARTIFACT, PENDING_MEMORY_ARTIFACT},
    }.get(request.tool_name)

    if required_types is not None and artifact_types != required_types:
        missing = sorted(required_types - artifact_types)
        unexpected = sorted(artifact_types - required_types)
        raise ArtifactContractError(
            f"artifact types do not match {request.tool_name}: "
            f"missing={missing}, unexpected={unexpected}"
        )


def _base_fields(request: ToolCallRequest) -> dict[str, str]:
    return {
        "task_id": request.task_id,
        "step_id": request.step_id,
        "request_id": request.request_id,
        "tool_name": request.tool_name,
    }


def _validate_pending_record_correlation(
    request: ToolCallRequest,
    record: PendingRecord,
) -> None:
    if record.request_id != request.request_id:
        raise ArtifactContractError("pending record request_id does not match request")

    if record.tool_name != request.tool_name:
        raise ArtifactContractError("pending record tool_name does not match request")


def _validate_tool_output_artifact(artifact: Mapping[str, Any]) -> None:
    status = _require_string(artifact, "status")
    try:
        ExecutionStatus(status)
    except ValueError as error:
        raise ArtifactContractError("tool_output status is not an ExecutionStatus") from error

    if _require_string(artifact, "content_type") != "application/json":
        raise ArtifactContractError("tool_output content_type must be application/json")

    if _require_string(artifact, "encoding") != "utf-8":
        raise ArtifactContractError("tool_output encoding must be utf-8")


def _artifact_identity(artifact: Mapping[str, Any]) -> tuple[str, str]:
    artifact_type = _require_string(artifact, "artifact_type")

    if artifact_type == TOOL_OUTPUT_ARTIFACT:
        identity_value = "singleton"
    elif artifact_type == PENDING_FILE_ARTIFACT:
        identity_value = _require_string(artifact, "path")
    elif artifact_type == PENDING_DELETE_ARTIFACT:
        identity_value = _require_string(artifact, "target_path")
    elif artifact_type == QUARANTINED_DOWNLOAD_ARTIFACT:
        identity_value = _require_string(artifact, "quarantine_path")
    elif artifact_type == PENDING_MEMORY_ARTIFACT:
        identity_value = _require_string(artifact, "memory_id")
    else:  # pragma: no cover - validate_artifact rejects this first.
        raise ArtifactContractError(f"unsupported artifact_type: {artifact_type}")

    return artifact_type, identity_value


def _require_string(artifact: Mapping[str, Any], field_name: str) -> str:
    value = artifact.get(field_name)
    if not isinstance(value, str) or not value or value.isspace():
        raise ArtifactContractError(f"artifact {field_name} must be a non-empty string")
    return value


def _require_sha256(artifact: Mapping[str, Any], field_name: str) -> str:
    value = _require_string(artifact, field_name)
    if not _SHA256_PATTERN.fullmatch(value):
        raise ArtifactContractError(f"artifact {field_name} must be lowercase SHA-256")
    return value


def _require_size(artifact: Mapping[str, Any], field_name: str) -> int:
    value = artifact.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ArtifactContractError(f"artifact {field_name} must be a non-negative integer")
    return value


def _require_exact_status(artifact: Mapping[str, Any], expected: str) -> None:
    actual = _require_string(artifact, "status")
    if actual != expected:
        raise ArtifactContractError(f"artifact status must be {expected}")


def _require_relative_path(artifact: Mapping[str, Any], field_name: str) -> str:
    value = _require_string(artifact, field_name)

    if "\\" in value or "\x00" in value:
        raise ArtifactContractError(f"artifact {field_name} must be a POSIX relative path")

    posix_path = PurePosixPath(value)
    windows_path = PureWindowsPath(value)

    if (
        posix_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.anchor)
        or ".." in posix_path.parts
        or value in {".", ".."}
        or posix_path.as_posix() != value
    ):
        raise ArtifactContractError(f"artifact {field_name} must be a normalized relative path")

    return value
