import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ra_agent.contracts import ExecutionStatus, ToolCallRequest, ToolExecutionResult
from ra_agent.execution.executor import RegistryToolExecutor
from ra_agent.execution.pending_store import PendingStore
from ra_agent.security.deep_checker import RuleBasedDeepSafetyChecker
from ra_agent.security.rule_engine import RuleEngine
from ra_agent.tools import DEFAULT_TOOL_SPECS
from ra_agent.tools.implementations.delete_file import DeleteFileHandler
from ra_agent.tools.implementations.write_file import WriteFileHandler
from ra_agent.tools.path_resolver import SafePathResolver
from ra_agent.tools.registry import ToolRegistry


def execution_result(
    request: ToolCallRequest,
    *,
    pending_changes: list[dict[str, object]] | None = None,
    output: object = None,
    status: ExecutionStatus = ExecutionStatus.PENDING_COMMIT,
    request_id: str | None = None,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request_id or request.request_id,
        checkpoint_id=f"checkpoint-{request.request_id}",
        status=status,
        output=output,
        pending_changes=pending_changes or [],
    )


def write_record(
    request: ToolCallRequest,
    pending_root: Path,
    *,
    target_path: str | None = None,
    payload_content: str | None = None,
) -> dict[str, object]:
    request_content = request.arguments.get("content")
    content = payload_content if payload_content is not None else request_content
    assert isinstance(content, str)
    payload = content.encode("utf-8")
    pending_path = f"{request.request_id}/payload.bin"
    payload_path = pending_root / pending_path
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    payload_path.write_bytes(payload)
    record = {
        "version": 1,
        "request_id": request.request_id,
        "checkpoint_id": f"checkpoint-{request.request_id}",
        "tool_name": "write_file",
        "operation": "WRITE",
        "target_path": target_path or str(request.arguments["path"]),
        "pending_path": pending_path,
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "size_bytes": len(payload),
        "created_at": datetime.now(UTC).isoformat(),
        "status": "PENDING",
    }
    (payload_path.parent / "manifest.json").write_text(
        json.dumps(record, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return record


def delete_record(request: ToolCallRequest, pending_root: Path) -> dict[str, object]:
    record = {
        "version": 1,
        "request_id": request.request_id,
        "checkpoint_id": f"checkpoint-{request.request_id}",
        "tool_name": "delete_file",
        "operation": "DELETE",
        "target_path": str(request.arguments["path"]),
        "pending_path": None,
        "content_sha256": None,
        "size_bytes": None,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "PENDING",
    }
    request_dir = pending_root / request.request_id
    request_dir.mkdir(parents=True, exist_ok=True)
    (request_dir / "manifest.json").write_text(
        json.dumps(record, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return record


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("write_file", {"path": "draft.txt", "content": "hello world"}),
        ("delete_file", {"path": "old.txt"}),
    ],
)
async def test_real_file_handler_pending_result_passes(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, object],
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    (workspace_root / "old.txt").write_text("keep until commit", encoding="utf-8")
    pending_store = PendingStore(tmp_path / "pending")
    resolver = SafePathResolver(
        workspace_root,
        max_path_length=4096,
        max_read_bytes=1024,
        max_write_bytes=1024,
    )
    handler = (
        WriteFileHandler(resolver, pending_store)
        if tool_name == "write_file"
        else DeleteFileHandler(resolver, pending_store)
    )
    registry = ToolRegistry()
    spec = next(spec for spec in DEFAULT_TOOL_SPECS if spec.name == tool_name)
    registry.register(spec, handler)
    request = request_factory(tool_name, arguments=arguments, request_id=f"real-{tool_name}")

    result = await RegistryToolExecutor(registry, pending_store).execute(
        request,
        checkpoint_id=f"checkpoint-{tool_name}",
    )
    checked = await RuleBasedDeepSafetyChecker(
        rules,
        workspace_root,
        pending_store.pending_root,
    ).check(request, result)

    assert checked.passed, checked.signals
    assert checked.signals == []
    assert (workspace_root / "old.txt").read_text(encoding="utf-8") == "keep until commit"
    assert not (workspace_root / "draft.txt").exists()


@pytest.mark.asyncio
async def test_inline_manifest_cannot_hide_divergent_authoritative_record(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
) -> None:
    request = request_factory(
        "write_file",
        arguments={"path": "safe.txt", "content": "safe content"},
        request_id="divergent-inline-manifest",
    )
    pending_store = PendingStore(tmp_path / "pending")
    stored = await pending_store.stage_write(
        request.request_id,
        "different.txt",
        b"safe content",
    )
    stored = await pending_store.bind_checkpoint(
        request.request_id,
        f"checkpoint-{request.request_id}",
    )
    inline_manifest = {
        "version": 1,
        "request_id": request.request_id,
        "checkpoint_id": stored.checkpoint_id,
        "tool_name": request.tool_name,
        "operation": stored.operation.value,
        "target_path": "safe.txt",
        "pending_path": stored.pending_path,
        "content_sha256": stored.content_sha256,
        "size_bytes": stored.size_bytes,
        "created_at": stored.created_at.isoformat(),
        "status": stored.status.value,
    }

    checked = await RuleBasedDeepSafetyChecker(
        rules,
        tmp_path / "workspace",
        pending_store.pending_root,
    ).check(
        request,
        execution_result(request, pending_changes=[inline_manifest]),
    )

    assert not checked.passed
    assert "pending_record_store_mismatch" in checked.signals


@pytest.mark.asyncio
async def test_safe_pending_write_v1_passes(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
) -> None:
    request = request_factory(
        "write_file", arguments={"path": "draft.txt", "content": "hello world"}
    )
    pending_root = tmp_path / "pending"
    result = execution_result(
        request,
        pending_changes=[write_record(request, pending_root)],
        output={"written": "draft.txt"},
    )

    checked = await RuleBasedDeepSafetyChecker(rules, tmp_path / "workspace", pending_root).check(
        request, result
    )

    assert checked.request_id == request.request_id
    assert checked.passed
    assert checked.signals == []


@pytest.mark.asyncio
async def test_safe_pending_delete_v1_passes(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
) -> None:
    request = request_factory("delete_file", arguments={"path": "old.txt"})
    pending_root = tmp_path / "pending"
    result = execution_result(request, pending_changes=[delete_record(request, pending_root)])

    checked = await RuleBasedDeepSafetyChecker(rules, tmp_path / "workspace", pending_root).check(
        request, result
    )

    assert checked.passed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_path", "actual_path", "expected_signal"),
    [
        (".env", ".env", "sensitive_path_modified"),
        ("../outside.txt", "../outside.txt", "workspace_escape"),
        ("draft.txt", "other.txt", "request_result_path_mismatch"),
        (
            "configs/risk_rules.yaml",
            "configs/risk_rules.yaml",
            "protected_path_modified",
        ),
    ],
)
async def test_unsafe_pending_paths_fail(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
    request_path: str,
    actual_path: str,
    expected_signal: str,
) -> None:
    request = request_factory("write_file", arguments={"path": request_path, "content": "safe"})
    pending_root = tmp_path / "pending"
    result = execution_result(
        request,
        pending_changes=[write_record(request, pending_root, target_path=actual_path)],
    )

    checked = await RuleBasedDeepSafetyChecker(rules, tmp_path / "workspace", pending_root).check(
        request, result
    )

    assert not checked.passed
    assert expected_signal in checked.signals


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value", "expected_signal"),
    [
        ("version", 2, "pending_record_version_invalid"),
        ("request_id", "other-request", "pending_request_id_mismatch"),
        ("checkpoint_id", None, "pending_checkpoint_unbound"),
        ("checkpoint_id", "wrong-checkpoint", "pending_checkpoint_mismatch"),
        ("tool_name", "delete_file", "pending_tool_name_mismatch"),
        ("operation", "write", "pending_operation_mismatch"),
        ("created_at", "2026-07-17T22:30:00", "pending_created_at_not_aware"),
        ("status", "COMMITTED", "pending_status_invalid"),
    ],
)
async def test_invalid_pending_record_fields_fail(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
    field: str,
    value: object,
    expected_signal: str,
) -> None:
    request = request_factory("write_file", arguments={"path": "draft.txt", "content": "safe"})
    pending_root = tmp_path / "pending"
    record = write_record(request, pending_root)
    record[field] = value
    result = execution_result(request, pending_changes=[record])

    checked = await RuleBasedDeepSafetyChecker(rules, tmp_path / "workspace", pending_root).check(
        request, result
    )

    assert not checked.passed
    assert expected_signal in checked.signals


@pytest.mark.asyncio
async def test_missing_or_extra_pending_record_fields_fail(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
) -> None:
    request = request_factory("write_file", arguments={"path": "draft.txt", "content": "safe"})
    pending_root = tmp_path / "pending"
    record = write_record(request, pending_root)
    del record["created_at"]
    record["unexpected"] = True

    checked = await RuleBasedDeepSafetyChecker(rules, tmp_path / "workspace", pending_root).check(
        request, execution_result(request, pending_changes=[record])
    )

    assert not checked.passed
    assert "pending_record_missing_fields" in checked.signals
    assert "pending_record_unexpected_fields" in checked.signals


@pytest.mark.asyncio
async def test_manifest_hash_and_size_must_match_request(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
) -> None:
    request = request_factory(
        "write_file", arguments={"path": "draft.txt", "content": "hello world"}
    )
    pending_root = tmp_path / "pending"
    record = write_record(request, pending_root)
    record["content_sha256"] = "0" * 64
    record["size_bytes"] = 99

    checked = await RuleBasedDeepSafetyChecker(rules, tmp_path / "workspace", pending_root).check(
        request, execution_result(request, pending_changes=[record])
    )

    assert not checked.passed
    assert "request_content_sha256_mismatch" in checked.signals
    assert "request_content_size_mismatch" in checked.signals


@pytest.mark.asyncio
async def test_tampered_payload_fails_hash_check(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
) -> None:
    request = request_factory(
        "write_file", arguments={"path": "draft.txt", "content": "hello world"}
    )
    pending_root = tmp_path / "pending"
    record = write_record(request, pending_root)
    (pending_root / str(record["pending_path"])).write_bytes(b"HELLO WORLD")

    checked = await RuleBasedDeepSafetyChecker(rules, tmp_path / "workspace", pending_root).check(
        request, execution_result(request, pending_changes=[record])
    )

    assert not checked.passed
    assert "pending_payload_sha256_mismatch" in checked.signals


@pytest.mark.asyncio
async def test_payload_path_must_use_request_namespace_and_pending_root(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
) -> None:
    request = request_factory("write_file", arguments={"path": "draft.txt", "content": "safe"})
    pending_root = tmp_path / "pending"
    record = write_record(request, pending_root)
    record["pending_path"] = "other-request/payload.bin"

    checked = await RuleBasedDeepSafetyChecker(rules, tmp_path / "workspace", pending_root).check(
        request, execution_result(request, pending_changes=[record])
    )

    assert not checked.passed
    assert "pending_payload_namespace_mismatch" in checked.signals


@pytest.mark.asyncio
async def test_pending_root_is_required_for_write_verification(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
) -> None:
    request = request_factory("write_file", arguments={"path": "draft.txt", "content": "safe"})
    record = write_record(request, tmp_path / "pending")

    checked = await RuleBasedDeepSafetyChecker(rules, tmp_path / "workspace").check(
        request, execution_result(request, pending_changes=[record])
    )

    assert not checked.passed
    assert "pending_root_unconfigured" in checked.signals


@pytest.mark.asyncio
async def test_delete_record_payload_fields_must_be_null(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
) -> None:
    request = request_factory("delete_file", arguments={"path": "old.txt"})
    record = delete_record(request, tmp_path / "pending")
    record.update(
        {
            "pending_path": "request/payload.bin",
            "content_sha256": "0" * 64,
            "size_bytes": 1,
        }
    )

    checked = await RuleBasedDeepSafetyChecker(
        rules, tmp_path / "workspace", tmp_path / "pending"
    ).check(request, execution_result(request, pending_changes=[record]))

    assert not checked.passed
    assert "delete_pending_path_must_be_null" in checked.signals
    assert "delete_content_sha256_must_be_null" in checked.signals
    assert "delete_size_must_be_null" in checked.signals


@pytest.mark.asyncio
async def test_secret_in_output_fails(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
) -> None:
    request = request_factory("write_file", arguments={"path": "draft.txt", "content": "safe"})
    pending_root = tmp_path / "pending"
    result = execution_result(
        request,
        pending_changes=[write_record(request, pending_root)],
        output={"token": "sk-1234567890abcdefghijkl"},
    )

    checked = await RuleBasedDeepSafetyChecker(rules, tmp_path / "workspace", pending_root).check(
        request, result
    )

    assert not checked.passed
    assert "secret_exposure" in checked.signals


@pytest.mark.asyncio
async def test_abnormal_change_count_fails(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
) -> None:
    request = request_factory("write_file", arguments={"path": "draft.txt", "content": "safe"})
    pending_root = tmp_path / "pending"
    record = write_record(request, pending_root)
    result = execution_result(request, pending_changes=[record, record.copy()])

    checked = await RuleBasedDeepSafetyChecker(rules, tmp_path / "workspace", pending_root).check(
        request, result
    )

    assert not checked.passed
    assert "unexpected_side_effect_count" in checked.signals


@pytest.mark.asyncio
async def test_non_pending_or_mismatched_result_fails(
    rules: RuleEngine,
    request_factory: Callable[..., ToolCallRequest],
    tmp_path: Path,
) -> None:
    request = request_factory("write_file", arguments={"path": "draft.txt", "content": "safe"})
    pending_root = tmp_path / "pending"
    result = execution_result(
        request,
        pending_changes=[write_record(request, pending_root)],
        status=ExecutionStatus.SUCCESS,
        request_id="different-request",
    )

    checked = await RuleBasedDeepSafetyChecker(rules, tmp_path / "workspace", pending_root).check(
        request, result
    )

    assert not checked.passed
    assert "correlation_mismatch" in checked.signals
    assert "unexpected_execution_status" in checked.signals
    assert checked.request_id == request.request_id


@pytest.mark.asyncio
async def test_invalid_configuration_rejects_pending_result(
    request_factory: Callable[..., ToolCallRequest], tmp_path: Path
) -> None:
    request = request_factory("write_file", arguments={"path": "draft.txt", "content": "safe"})
    record = write_record(request, tmp_path / "pending")
    result = execution_result(request, pending_changes=[record])

    checked = await RuleBasedDeepSafetyChecker(
        RuleEngine.invalid("broken"), tmp_path / "workspace", tmp_path / "pending"
    ).check(request, result)

    assert not checked.passed
    assert checked.signals == ["configuration_invalid"]
