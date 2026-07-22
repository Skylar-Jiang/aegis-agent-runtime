from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import pytest

from ra_agent.contracts import (
    ExecutionStatus,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution.artifacts import (
    ArtifactContractError,
    build_pending_delete_artifact,
    build_pending_file_artifact,
    build_pending_memory_artifact,
    build_quarantined_download_artifact,
    build_tool_output_artifact,
    canonical_json_bytes,
    validate_artifact,
    validate_execution_artifacts,
)
from ra_agent.execution.pending_store import (
    PendingOperation,
    PendingRecord,
    PendingStatus,
)


def make_request(
    tool_name: str,
    *,
    request_id: str = "request-artifact",
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id=request_id,
        tool_name=tool_name,
        arguments={},
        objective="create inspectable execution artifacts",
        context_summary="artifact schema unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def make_pending_write(request: ToolCallRequest) -> PendingRecord:
    payload = b"pending content"
    return PendingRecord(
        request_id=request.request_id,
        checkpoint_id=None,
        tool_name=request.tool_name,
        operation=PendingOperation.WRITE,
        target_path="reports/result.md",
        pending_path=f"{request.request_id}/payload.bin",
        content_sha256=sha256(payload).hexdigest(),
        size_bytes=len(payload),
        created_at=datetime.now(UTC),
        status=PendingStatus.PENDING,
    )


def make_pending_delete(request: ToolCallRequest) -> PendingRecord:
    return PendingRecord(
        request_id=request.request_id,
        checkpoint_id=None,
        tool_name=request.tool_name,
        operation=PendingOperation.DELETE,
        target_path="reports/old.md",
        pending_path=None,
        content_sha256=None,
        size_bytes=None,
        created_at=datetime.now(UTC),
        status=PendingStatus.PENDING,
    )


def test_tool_output_artifact_contains_correlation_and_digest() -> None:
    request = make_request("list_dir")
    output = {"entries": [], "path": "."}

    artifact = build_tool_output_artifact(
        request,
        output,
        status=ExecutionStatus.SUCCESS,
    )

    payload = canonical_json_bytes(output)
    assert artifact == {
        "artifact_type": "tool_output",
        "task_id": request.task_id,
        "step_id": request.step_id,
        "request_id": request.request_id,
        "tool_name": request.tool_name,
        "status": "SUCCESS",
        "content_type": "application/json",
        "encoding": "utf-8",
        "sha256": sha256(payload).hexdigest(),
        "size_bytes": len(payload),
    }


def test_tool_output_hash_is_stable_across_dictionary_order() -> None:
    request = make_request("list_dir")

    first = build_tool_output_artifact(
        request,
        {"a": 1, "b": 2},
        status=ExecutionStatus.SUCCESS,
    )
    second = build_tool_output_artifact(
        request,
        {"b": 2, "a": 1},
        status=ExecutionStatus.SUCCESS,
    )

    assert first == second


def test_tool_output_hash_changes_when_output_changes() -> None:
    request = make_request("list_dir")
    first = build_tool_output_artifact(
        request,
        {"value": 1},
        status=ExecutionStatus.SUCCESS,
    )
    second = build_tool_output_artifact(
        request,
        {"value": 2},
        status=ExecutionStatus.SUCCESS,
    )

    assert first["sha256"] != second["sha256"]


@pytest.mark.parametrize("output", [{"invalid": {1, 2}}, {"invalid": float("nan")}])
def test_non_json_or_non_finite_tool_output_is_rejected(output: object) -> None:
    with pytest.raises(ArtifactContractError):
        build_tool_output_artifact(
            make_request("list_dir"),
            output,
            status=ExecutionStatus.SUCCESS,
        )


def test_pending_file_artifact_uses_only_relative_paths() -> None:
    request = make_request("write_file")
    record = make_pending_write(request)

    artifact = build_pending_file_artifact(request, record)

    assert artifact["artifact_type"] == "pending_file"
    assert artifact["status"] == "PENDING"
    assert artifact["path"] == f"{request.request_id}/payload.bin"
    assert artifact["target_path"] == "reports/result.md"
    assert artifact["sha256"] == record.content_sha256
    assert artifact["size_bytes"] == record.size_bytes


def test_pending_delete_artifact_has_deterministic_marker_digest() -> None:
    request = make_request("delete_file")
    record = make_pending_delete(request)

    first = build_pending_delete_artifact(request, record)
    second = build_pending_delete_artifact(request, record)

    assert first == second
    assert first["artifact_type"] == "pending_delete"
    assert first["status"] == "PENDING"
    assert first["target_path"] == "reports/old.md"
    assert isinstance(first["size_bytes"], int)
    assert first["size_bytes"] > 0


def test_quarantined_download_artifact_is_not_trusted() -> None:
    request = make_request("download_url")
    artifact = build_quarantined_download_artifact(
        request,
        quarantine_path=f"{request.request_id}/payload.bin",
        source_url="https://example.test/source",
        final_url="https://example.test/final",
        content_sha256="a" * 64,
        size_bytes=12,
        content_type="application/octet-stream",
    )

    assert artifact["artifact_type"] == "quarantined_download"
    assert artifact["status"] == "QUARANTINED"
    assert "trusted" not in artifact


def test_pending_memory_artifact_is_not_trusted() -> None:
    request = make_request("memory_write")
    artifact = build_pending_memory_artifact(
        request,
        memory_id="memory-1",
        content_sha256="b" * 64,
        size_bytes=20,
    )

    assert artifact["artifact_type"] == "pending_memory"
    assert artifact["status"] == "PENDING"
    assert "trusted" not in artifact


@pytest.mark.parametrize(
    "path",
    [
        "/tmp/payload.bin",
        "C:/Users/test/payload.bin",
        "C:\\Users\\test\\payload.bin",
        "../payload.bin",
        "request/../payload.bin",
        "request\\payload.bin",
        "request//payload.bin",
        ".",
    ],
)
def test_artifact_rejects_non_portable_or_unsafe_paths(path: str) -> None:
    artifact: dict[str, Any] = {
        "artifact_type": "pending_file",
        "task_id": "task-1",
        "step_id": "step-1",
        "request_id": "request-1",
        "tool_name": "write_file",
        "status": "PENDING",
        "path": path,
        "target_path": "reports/result.md",
        "sha256": "a" * 64,
        "size_bytes": 1,
    }

    with pytest.raises(ArtifactContractError):
        validate_artifact(artifact)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("sha256", "A" * 64),
        ("sha256", "a" * 63),
        ("size_bytes", -1),
        ("size_bytes", True),
    ],
)
def test_artifact_rejects_invalid_integrity_metadata(
    field: str,
    replacement: object,
) -> None:
    request = make_request("list_dir")
    artifact = build_tool_output_artifact(
        request,
        {"ok": True},
        status=ExecutionStatus.SUCCESS,
    )
    artifact[field] = replacement

    with pytest.raises(ArtifactContractError):
        validate_artifact(artifact)


@pytest.mark.parametrize(
    ("artifact_type", "status"),
    [
        ("pending_file", "TRUSTED"),
        ("pending_delete", "COMMITTED"),
        ("quarantined_download", "TRUSTED"),
        ("pending_memory", "TRUSTED"),
    ],
)
def test_untrusted_artifacts_reject_trusted_or_committed_status(
    artifact_type: str,
    status: str,
) -> None:
    common: dict[str, Any] = {
        "artifact_type": artifact_type,
        "task_id": "task-1",
        "step_id": "step-1",
        "request_id": "request-1",
        "tool_name": "write_file",
        "status": status,
        "sha256": "a" * 64,
        "size_bytes": 1,
    }
    if artifact_type == "pending_file":
        common.update(path="request-1/payload.bin", target_path="reports/result.md")
    elif artifact_type == "pending_delete":
        common["target_path"] = "reports/result.md"
    elif artifact_type == "quarantined_download":
        common.update(
            quarantine_path="request-1/payload.bin",
            source_url="https://example.test/source",
            final_url="https://example.test/final",
            content_type="application/octet-stream",
        )
    else:
        common["memory_id"] = "memory-1"

    with pytest.raises(ArtifactContractError):
        validate_artifact(common)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("task_id", "other-task"),
        ("step_id", "other-step"),
        ("request_id", "other-request"),
        ("tool_name", "other-tool"),
    ],
)
def test_execution_rejects_artifact_correlation_mismatch(
    field: str,
    replacement: str,
) -> None:
    request = make_request("list_dir")
    output = {"entries": []}
    artifact = build_tool_output_artifact(
        request,
        output,
        status=ExecutionStatus.SUCCESS,
    )
    artifact[field] = replacement
    execution = ToolExecutionResult(
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        status=ExecutionStatus.SUCCESS,
        output=output,
        artifacts=[artifact],
    )

    with pytest.raises(ArtifactContractError):
        validate_execution_artifacts(request, execution)


def test_execution_rejects_stale_tool_output_digest() -> None:
    request = make_request("list_dir")
    artifact = build_tool_output_artifact(
        request,
        {"value": "old"},
        status=ExecutionStatus.SUCCESS,
    )
    execution = ToolExecutionResult(
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        status=ExecutionStatus.SUCCESS,
        output={"value": "new"},
        artifacts=[artifact],
    )

    with pytest.raises(ArtifactContractError, match="tool_output"):
        validate_execution_artifacts(request, execution)


def test_execution_rejects_missing_write_companion_artifact() -> None:
    request = make_request("write_file")
    output = {"staged": True}
    execution = ToolExecutionResult(
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        status=ExecutionStatus.PENDING_COMMIT,
        output=output,
        artifacts=[
            build_tool_output_artifact(
                request,
                output,
                status=ExecutionStatus.PENDING_COMMIT,
            )
        ],
    )

    with pytest.raises(ArtifactContractError, match="artifact types"):
        validate_execution_artifacts(request, execution)


def test_execution_rejects_duplicate_artifact_identity() -> None:
    request = make_request("list_dir")
    output = {"entries": []}
    artifact = build_tool_output_artifact(
        request,
        output,
        status=ExecutionStatus.SUCCESS,
    )
    execution = ToolExecutionResult(
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        status=ExecutionStatus.SUCCESS,
        output=output,
        artifacts=[artifact, dict(artifact)],
    )

    with pytest.raises(ArtifactContractError, match="duplicate"):
        validate_execution_artifacts(request, execution)


def test_empty_artifact_list_remains_compatible_with_orchestration_mocks() -> None:
    request = make_request("list_dir")
    execution = ToolExecutionResult(
        task_id="mock-task",
        step_id="mock-step",
        request_id="mock-request",
        status=ExecutionStatus.SUCCESS,
        output={"mock": True},
    )

    validate_execution_artifacts(request, execution)


def test_pending_memory_artifact_can_expose_safe_inspection_path_and_key() -> None:
    request = make_request("memory_write", request_id="request-memory-inspection")

    artifact = build_pending_memory_artifact(
        request,
        memory_id="memory-request-memory-inspection",
        key="project.note",
        path="records/request-memory-inspection/payload.json",
        content_sha256="c" * 64,
        size_bytes=21,
    )

    assert artifact["key"] == "project.note"
    assert artifact["path"] == "records/request-memory-inspection/payload.json"
    validate_artifact(artifact)


@pytest.mark.parametrize(
    ("key", "path"),
    [
        ("project.note", None),
        (None, "records/request/payload.json"),
        ("project.note", "../payload.json"),
        ("project.note", "C:/memory/payload.json"),
    ],
)
def test_pending_memory_inspection_metadata_fails_closed(
    key: str | None,
    path: str | None,
) -> None:
    request = make_request("memory_write", request_id="request-memory-invalid-path")

    with pytest.raises(ArtifactContractError):
        build_pending_memory_artifact(
            request,
            memory_id="memory-request-memory-invalid-path",
            key=key,
            path=path,
            content_sha256="d" * 64,
            size_bytes=10,
        )
