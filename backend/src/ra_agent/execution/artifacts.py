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
from .process_runner import ProcessExecutionRecord

TOOL_OUTPUT_ARTIFACT = "tool_output"
PENDING_FILE_ARTIFACT = "pending_file"
PENDING_DELETE_ARTIFACT = "pending_delete"
QUARANTINED_DOWNLOAD_ARTIFACT = "quarantined_download"
PENDING_MEMORY_ARTIFACT = "pending_memory"
SHELL_EXECUTION_ARTIFACT = "shell_execution"

_ALLOWED_ARTIFACT_TYPES = frozenset(
    {
        TOOL_OUTPUT_ARTIFACT,
        PENDING_FILE_ARTIFACT,
        PENDING_DELETE_ARTIFACT,
        QUARANTINED_DOWNLOAD_ARTIFACT,
        PENDING_MEMORY_ARTIFACT,
        SHELL_EXECUTION_ARTIFACT,
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
    key: str | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    """Build the inspectable metadata boundary for untrusted pending memory."""

    if request.tool_name != "memory_write":
        raise ArtifactContractError("pending memory artifact requires memory_write")

    if (key is None) != (path is None):
        raise ArtifactContractError("pending memory key and path must be provided together")

    artifact: dict[str, Any] = {
        **_base_fields(request),
        "artifact_type": PENDING_MEMORY_ARTIFACT,
        "status": MemoryStatus.PENDING.value,
        "memory_id": memory_id,
        "sha256": content_sha256,
        "size_bytes": size_bytes,
    }
    if key is not None and path is not None:
        artifact["key"] = key
        artifact["path"] = path

    validate_artifact(artifact)
    return artifact


def build_shell_execution_artifact(
    request: ToolCallRequest,
    *,
    command: str,
    record: ProcessExecutionRecord,
    status: ExecutionStatus,
) -> dict[str, Any]:
    """Build inspectable evidence for one restricted subprocess execution."""

    if request.tool_name != "run_shell":
        raise ArtifactContractError("shell execution artifact requires run_shell")
    command_bytes = command.encode("utf-8")
    stdout_bytes = record.stdout.encode("utf-8")
    stderr_bytes = record.stderr.encode("utf-8")
    combined = stdout_bytes + b"\x00" + stderr_bytes
    artifact: dict[str, Any] = {
        **_base_fields(request),
        "artifact_type": SHELL_EXECUTION_ARTIFACT,
        "status": status.value,
        "executable": record.executable,
        "arguments": list(record.arguments),
        "cwd": record.cwd,
        "exit_code": record.exit_code,
        "duration_ms": record.duration_ms,
        "stdout_sha256": record.stdout_sha256,
        "stdout_size_bytes": record.stdout_size_bytes,
        "stderr_sha256": record.stderr_sha256,
        "stderr_size_bytes": record.stderr_size_bytes,
        "environment_keys": list(record.environment_keys),
        "command_sha256": sha256(command_bytes).hexdigest(),
        "command_size_bytes": len(command_bytes),
        "timed_out": False,
        "output_truncated": False,
        "sha256": sha256(combined).hexdigest(),
        "size_bytes": len(combined),
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
        key = artifact.get("key")
        path = artifact.get("path")
        if (key is None) != (path is None):
            raise ArtifactContractError(
                "pending memory artifact key and path must be provided together"
            )
        if key is not None:
            _require_string(artifact, "key")
            _require_relative_path(artifact, "path")
    elif artifact_type == SHELL_EXECUTION_ARTIFACT:
        _validate_shell_execution_artifact(artifact)


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

    shell_execution = next(
        (
            artifact
            for artifact in execution.artifacts
            if artifact.get("artifact_type") == SHELL_EXECUTION_ARTIFACT
        ),
        None,
    )
    if shell_execution is not None:
        _validate_shell_execution_against_output(request, execution, shell_execution)

    required_types = {
        "list_dir": {TOOL_OUTPUT_ARTIFACT},
        "read_file": {TOOL_OUTPUT_ARTIFACT},
        "write_file": {TOOL_OUTPUT_ARTIFACT, PENDING_FILE_ARTIFACT},
        "delete_file": {TOOL_OUTPUT_ARTIFACT, PENDING_DELETE_ARTIFACT},
        "download_url": {TOOL_OUTPUT_ARTIFACT, QUARANTINED_DOWNLOAD_ARTIFACT},
        "memory_read": {TOOL_OUTPUT_ARTIFACT},
        "memory_write": {TOOL_OUTPUT_ARTIFACT, PENDING_MEMORY_ARTIFACT},
        "run_shell": {TOOL_OUTPUT_ARTIFACT, SHELL_EXECUTION_ARTIFACT},
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

    if artifact_type in {TOOL_OUTPUT_ARTIFACT, SHELL_EXECUTION_ARTIFACT}:
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


def _validate_shell_execution_artifact(artifact: Mapping[str, Any]) -> None:
    status = _require_string(artifact, "status")
    try:
        ExecutionStatus(status)
    except ValueError as error:
        raise ArtifactContractError("shell execution status is invalid") from error
    executable = _require_string(artifact, "executable")
    if "/" in executable or "\\" in executable:
        raise ArtifactContractError("shell artifact executable must be a logical name")
    arguments = artifact.get("arguments")
    if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
        raise ArtifactContractError("shell artifact arguments must be a string list")
    _require_workspace_cwd(artifact, "cwd")
    exit_code = artifact.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise ArtifactContractError("shell artifact exit_code must be an integer")
    for field_name in (
        "duration_ms",
        "stdout_size_bytes",
        "stderr_size_bytes",
        "command_size_bytes",
    ):
        _require_size(artifact, field_name)
    for field_name in ("stdout_sha256", "stderr_sha256", "command_sha256"):
        _require_sha256(artifact, field_name)
    keys = artifact.get("environment_keys")
    if not isinstance(keys, list) or not all(isinstance(item, str) and item for item in keys):
        raise ArtifactContractError("shell artifact environment_keys must be strings")
    if len(keys) != len(set(keys)):
        raise ArtifactContractError("shell artifact environment_keys must be unique")
    if artifact.get("timed_out") is not False:
        raise ArtifactContractError("completed shell artifact must not be timed out")
    if artifact.get("output_truncated") is not False:
        raise ArtifactContractError("truncated shell output is not accepted")


def _validate_shell_execution_against_output(
    request: ToolCallRequest,
    execution: ToolExecutionResult,
    artifact: Mapping[str, Any],
) -> None:
    if request.tool_name != "run_shell":
        raise ArtifactContractError("shell execution artifact is only valid for run_shell")
    if not isinstance(execution.output, Mapping):
        raise ArtifactContractError("run_shell output must be a mapping")
    output = execution.output
    for field_name in (
        "executable",
        "arguments",
        "cwd",
        "exit_code",
        "duration_ms",
        "environment_keys",
    ):
        if artifact.get(field_name) != output.get(field_name):
            raise ArtifactContractError(f"shell artifact {field_name} does not match output")
    stdout = output.get("stdout")
    stderr = output.get("stderr")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise ArtifactContractError("run_shell stdout and stderr must be strings")
    stdout_bytes = stdout.encode("utf-8")
    stderr_bytes = stderr.encode("utf-8")
    if artifact.get("stdout_size_bytes") != len(stdout_bytes):
        raise ArtifactContractError("shell stdout size does not match output")
    if artifact.get("stderr_size_bytes") != len(stderr_bytes):
        raise ArtifactContractError("shell stderr size does not match output")
    if artifact.get("stdout_sha256") != sha256(stdout_bytes).hexdigest():
        raise ArtifactContractError("shell stdout hash does not match output")
    if artifact.get("stderr_sha256") != sha256(stderr_bytes).hexdigest():
        raise ArtifactContractError("shell stderr hash does not match output")
    combined = stdout_bytes + b"\x00" + stderr_bytes
    if artifact.get("size_bytes") != len(combined):
        raise ArtifactContractError("shell combined output size does not match output")
    if artifact.get("sha256") != sha256(combined).hexdigest():
        raise ArtifactContractError("shell combined output hash does not match output")
    command = request.arguments.get("command")
    if not isinstance(command, str):
        raise ArtifactContractError("run_shell command must be a string")
    command_bytes = command.encode("utf-8")
    if artifact.get("command_size_bytes") != len(command_bytes):
        raise ArtifactContractError("shell command size does not match request")
    if artifact.get("command_sha256") != sha256(command_bytes).hexdigest():
        raise ArtifactContractError("shell command hash does not match request")
    expected_status = (
        ExecutionStatus.SUCCESS if output.get("exit_code") == 0 else ExecutionStatus.FAILED
    )
    if execution.status is not expected_status or artifact.get("status") != expected_status.value:
        raise ArtifactContractError("shell execution status does not match exit_code")


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


def _require_workspace_cwd(artifact: Mapping[str, Any], field_name: str) -> str:
    value = _require_string(artifact, field_name)
    if value == ".":
        return value
    return _require_relative_path(artifact, field_name)


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
