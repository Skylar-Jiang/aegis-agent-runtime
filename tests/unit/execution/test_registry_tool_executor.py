from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ra_agent.contracts import (
    ApprovalDecision,
    ApprovalStatus,
    ExecutionStatus,
    MemoryStatus,
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
    ToolSpec,
)
from ra_agent.execution.artifacts import (
    build_quarantined_download_artifact,
    build_tool_output_artifact,
)
from ra_agent.execution.executor import (
    ApprovalContextError,
    CheckpointRequiredError,
    InvalidToolSpecError,
    MissingToolHandlerError,
    PendingResourceUnavailableError,
    RegistryToolExecutor,
    ToolDisabledError,
    ToolExecutionTimeoutError,
    ToolResultContractError,
    UnknownToolError,
)
from ra_agent.execution.pending_store import PendingConflictError, PendingStore
from ra_agent.execution.quarantine import (
    FilesystemQuarantineStore,
    QuarantineStatus,
)
from ra_agent.memory import FilesystemMemoryStore
from ra_agent.tools.implementations.memory_tools import MemoryWriteHandler
from ra_agent.tools.implementations.write_file import WriteFileHandler
from ra_agent.tools.path_resolver import SafePathResolver
from ra_agent.tools.registry import ToolRegistry
from ra_agent.tools.specs import DEFAULT_TOOL_SPECS


class StubHandler:
    def __init__(
        self,
        *,
        status: ExecutionStatus = ExecutionStatus.SUCCESS,
        delay_seconds: float = 0,
        pending_changes: list[dict[str, Any]] | None = None,
    ) -> None:
        self.status = status
        self.delay_seconds = delay_seconds
        self.pending_changes = pending_changes or []
        self.call_count = 0

    async def __call__(
        self,
        request: ToolCallRequest,
    ) -> ToolExecutionResult:
        self.call_count += 1

        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)

        # Deliberately return incorrect correlation IDs so the test can verify
        # that RegistryToolExecutor replaces them with trusted request values.
        return ToolExecutionResult(
            task_id="handler-task",
            step_id="handler-step",
            request_id="handler-request",
            status=self.status,
            pending_changes=self.pending_changes,
            output={"handler": True},
        )


class ArtifactStubHandler:
    def __init__(
        self,
        *,
        mutations: dict[str, object] | None = None,
        output: object | None = None,
    ) -> None:
        self.mutations = mutations or {}
        self.output = {"handler": True} if output is None else output
        self.call_count = 0

    async def __call__(
        self,
        request: ToolCallRequest,
    ) -> ToolExecutionResult:
        self.call_count += 1
        artifact = build_tool_output_artifact(
            request,
            self.output,
            status=ExecutionStatus.SUCCESS,
        )
        artifact.update(self.mutations)
        return ToolExecutionResult(
            task_id="handler-task",
            step_id="handler-step",
            request_id="handler-request",
            status=ExecutionStatus.SUCCESS,
            output=self.output,
            artifacts=[artifact],
        )


class ApprovalAwareStubHandler(StubHandler):
    def __init__(self) -> None:
        super().__init__()
        self.approved_call_count = 0

    async def execute_approved(
        self,
        request: ToolCallRequest,
        approval_decision: ApprovalDecision,
    ) -> ToolExecutionResult:
        self.approved_call_count += 1

        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.SUCCESS,
            output={
                "approved": True,
                "approval_id": approval_decision.approval_id,
            },
        )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    return root


@pytest.fixture
def pending_store(tmp_path: Path) -> PendingStore:
    return PendingStore(tmp_path / "pending")


def get_spec(tool_name: str) -> ToolSpec:
    return next(spec for spec in DEFAULT_TOOL_SPECS if spec.name == tool_name)


def make_request(
    tool_name: str,
    *,
    arguments: dict[str, object],
    request_id: str,
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id=request_id,
        tool_name=tool_name,
        arguments=arguments,
        objective="execute a tool safely",
        context_summary="registry executor unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


def make_approval_decision(
    request: ToolCallRequest,
    *,
    status: ApprovalStatus,
    task_id: str | None = None,
    step_id: str | None = None,
    request_id: str | None = None,
) -> ApprovalDecision:
    return ApprovalDecision(
        approval_id="approval-1",
        task_id=task_id or request.task_id,
        step_id=step_id or request.step_id,
        request_id=request_id or request.request_id,
        status=status,
        decided_by="unit-test",
        decided_at=datetime.now(UTC),
        reason="approval decision for unit test",
    )


@pytest.mark.asyncio
async def test_executor_normalizes_correlation_and_timestamps(
    pending_store: PendingStore,
) -> None:
    registry = ToolRegistry()
    handler = StubHandler()
    registry.register(get_spec("list_dir"), handler)

    executor = RegistryToolExecutor(registry, pending_store)
    request = make_request(
        "list_dir",
        arguments={"path": "."},
        request_id="request-1",
    )

    result = await executor.execute(request)

    assert handler.call_count == 1
    assert result.status is ExecutionStatus.SUCCESS
    assert result.task_id == request.task_id
    assert result.step_id == request.step_id
    assert result.request_id == request.request_id
    assert result.checkpoint_id is None
    assert result.started_at is not None
    assert result.finished_at is not None
    assert result.started_at <= result.finished_at


@pytest.mark.asyncio
async def test_executor_accepts_valid_inspectable_artifacts(
    pending_store: PendingStore,
) -> None:
    registry = ToolRegistry()
    handler = ArtifactStubHandler()
    registry.register(get_spec("list_dir"), handler)
    executor = RegistryToolExecutor(registry, pending_store)
    request = make_request(
        "list_dir",
        arguments={"path": "."},
        request_id="request-valid-artifact",
    )

    result = await executor.execute(request)

    assert handler.call_count == 1
    assert result.request_id == request.request_id
    assert result.artifacts[0]["request_id"] == request.request_id
    assert result.artifacts[0]["tool_name"] == request.tool_name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("task_id", "other-task"),
        ("step_id", "other-step"),
        ("request_id", "other-request"),
        ("tool_name", "read_file"),
    ],
)
async def test_executor_rejects_artifact_correlation_mismatch(
    pending_store: PendingStore,
    field: str,
    replacement: str,
) -> None:
    registry = ToolRegistry()
    handler = ArtifactStubHandler(mutations={field: replacement})
    registry.register(get_spec("list_dir"), handler)
    executor = RegistryToolExecutor(registry, pending_store)

    with pytest.raises(ToolResultContractError, match="invalid execution artifacts"):
        await executor.execute(
            make_request(
                "list_dir",
                arguments={"path": "."},
                request_id=f"request-artifact-{field}",
            )
        )


@pytest.mark.asyncio
async def test_executor_rejects_stale_tool_output_hash(
    pending_store: PendingStore,
) -> None:
    registry = ToolRegistry()
    handler = ArtifactStubHandler(mutations={"sha256": "0" * 64})
    registry.register(get_spec("list_dir"), handler)
    executor = RegistryToolExecutor(registry, pending_store)

    with pytest.raises(ToolResultContractError, match="tool_output hash"):
        await executor.execute(
            make_request(
                "list_dir",
                arguments={"path": "."},
                request_id="request-stale-output-artifact",
            )
        )


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected(
    pending_store: PendingStore,
) -> None:
    executor = RegistryToolExecutor(ToolRegistry(), pending_store)

    with pytest.raises(UnknownToolError):
        await executor.execute(
            make_request(
                "unknown_tool",
                arguments={},
                request_id="request-unknown",
            )
        )


@pytest.mark.asyncio
async def test_missing_handler_is_rejected(
    pending_store: PendingStore,
) -> None:
    registry = ToolRegistry()
    registry.register(get_spec("list_dir"))
    executor = RegistryToolExecutor(registry, pending_store)

    with pytest.raises(MissingToolHandlerError):
        await executor.execute(
            make_request(
                "list_dir",
                arguments={"path": "."},
                request_id="request-missing-handler",
            )
        )


@pytest.mark.asyncio
async def test_disabled_tool_is_not_executed(
    pending_store: PendingStore,
) -> None:
    registry = ToolRegistry()
    handler = StubHandler()
    registry.register(get_spec("run_shell"), handler)

    executor = RegistryToolExecutor(registry, pending_store)

    with pytest.raises(ToolDisabledError):
        await executor.execute(
            make_request(
                "run_shell",
                arguments={"command": "echo test"},
                request_id="request-shell",
            )
        )

    assert handler.call_count == 0


@pytest.mark.asyncio
async def test_invalid_timeout_is_rejected(
    pending_store: PendingStore,
) -> None:
    registry = ToolRegistry()
    handler = StubHandler()
    invalid_spec = get_spec("list_dir").model_copy(update={"timeout_seconds": 0})
    registry.register(invalid_spec, handler)

    executor = RegistryToolExecutor(registry, pending_store)

    with pytest.raises(InvalidToolSpecError):
        await executor.execute(
            make_request(
                "list_dir",
                arguments={"path": "."},
                request_id="request-invalid-timeout",
            )
        )

    assert handler.call_count == 0


@pytest.mark.asyncio
async def test_handler_timeout_is_raised(
    pending_store: PendingStore,
) -> None:
    registry = ToolRegistry()
    handler = StubHandler(delay_seconds=0.1)
    short_spec = get_spec("list_dir").model_copy(update={"timeout_seconds": 0.01})
    registry.register(short_spec, handler)

    executor = RegistryToolExecutor(registry, pending_store)

    with pytest.raises(ToolExecutionTimeoutError):
        await executor.execute(
            make_request(
                "list_dir",
                arguments={"path": "."},
                request_id="request-timeout",
            )
        )


@pytest.mark.asyncio
async def test_pending_write_binds_checkpoint(
    workspace: Path,
    pending_store: PendingStore,
) -> None:
    (workspace / "reports").mkdir()

    resolver = SafePathResolver(
        workspace,
        max_path_length=4096,
        max_read_bytes=1024,
        max_write_bytes=1024,
    )
    handler = WriteFileHandler(resolver, pending_store)

    registry = ToolRegistry()
    registry.register(get_spec("write_file"), handler)

    executor = RegistryToolExecutor(registry, pending_store)
    request = make_request(
        "write_file",
        arguments={
            "path": "reports/result.md",
            "content": "new content",
        },
        request_id="request-write",
    )

    result = await executor.execute(
        request,
        checkpoint_id="checkpoint-write",
    )

    assert result.status is ExecutionStatus.PENDING_COMMIT
    assert result.task_id == request.task_id
    assert result.step_id == request.step_id
    assert result.request_id == request.request_id
    assert result.checkpoint_id == "checkpoint-write"
    assert result.started_at is not None
    assert result.finished_at is not None

    record = await pending_store.get("request-write")
    assert record.checkpoint_id == "checkpoint-write"
    assert await pending_store.verify_integrity("request-write") is True

    assert not (workspace / "reports" / "result.md").exists()


@pytest.mark.asyncio
async def test_repeated_pending_write_reuses_same_pending_record(
    workspace: Path,
    pending_store: PendingStore,
) -> None:
    (workspace / "reports").mkdir()

    resolver = SafePathResolver(
        workspace,
        max_path_length=4096,
        max_read_bytes=1024,
        max_write_bytes=1024,
    )
    handler = WriteFileHandler(resolver, pending_store)

    registry = ToolRegistry()
    registry.register(get_spec("write_file"), handler)
    executor = RegistryToolExecutor(registry, pending_store)

    request = make_request(
        "write_file",
        arguments={
            "path": "reports/result.md",
            "content": "same content",
        },
        request_id="request-idempotent",
    )

    first = await executor.execute(
        request,
        checkpoint_id="checkpoint-idempotent",
    )
    second = await executor.execute(
        request,
        checkpoint_id="checkpoint-idempotent",
    )

    assert first.pending_changes == second.pending_changes

    request_dir = pending_store.pending_root / "request-idempotent"
    assert {path.name for path in request_dir.iterdir()} == {
        "manifest.json",
        "payload.bin",
    }


@pytest.mark.asyncio
async def test_reused_request_with_different_content_is_rejected(
    workspace: Path,
    pending_store: PendingStore,
) -> None:
    (workspace / "reports").mkdir()

    resolver = SafePathResolver(
        workspace,
        max_path_length=4096,
        max_read_bytes=1024,
        max_write_bytes=1024,
    )
    handler = WriteFileHandler(resolver, pending_store)

    registry = ToolRegistry()
    registry.register(get_spec("write_file"), handler)
    executor = RegistryToolExecutor(registry, pending_store)

    await executor.execute(
        make_request(
            "write_file",
            arguments={
                "path": "reports/result.md",
                "content": "first",
            },
            request_id="request-conflict",
        ),
        checkpoint_id="checkpoint-conflict",
    )

    with pytest.raises(PendingConflictError):
        await executor.execute(
            make_request(
                "write_file",
                arguments={
                    "path": "reports/result.md",
                    "content": "second",
                },
                request_id="request-conflict",
            ),
            checkpoint_id="checkpoint-conflict",
        )


@pytest.mark.asyncio
async def test_pending_tool_requires_checkpoint(
    workspace: Path,
    pending_store: PendingStore,
) -> None:
    (workspace / "reports").mkdir()

    resolver = SafePathResolver(
        workspace,
        max_path_length=4096,
        max_read_bytes=1024,
        max_write_bytes=1024,
    )

    registry = ToolRegistry()
    registry.register(
        get_spec("write_file"),
        WriteFileHandler(resolver, pending_store),
    )

    executor = RegistryToolExecutor(registry, pending_store)

    with pytest.raises(CheckpointRequiredError):
        await executor.execute(
            make_request(
                "write_file",
                arguments={
                    "path": "reports/result.md",
                    "content": "content",
                },
                request_id="request-no-checkpoint",
            )
        )

    assert not (pending_store.pending_root / "request-no-checkpoint").exists()


@pytest.mark.asyncio
async def test_non_pending_tool_cannot_return_pending_commit(
    pending_store: PendingStore,
) -> None:
    registry = ToolRegistry()
    handler = StubHandler(
        status=ExecutionStatus.PENDING_COMMIT,
        pending_changes=[
            {
                "operation": "WRITE",
                "target_path": "result.txt",
            }
        ],
    )
    registry.register(get_spec("list_dir"), handler)

    executor = RegistryToolExecutor(registry, pending_store)

    with pytest.raises(ToolResultContractError):
        await executor.execute(
            make_request(
                "list_dir",
                arguments={"path": "."},
                request_id="request-fake-pending",
            )
        )


@pytest.mark.asyncio
async def test_pending_tool_cannot_return_success(
    pending_store: PendingStore,
) -> None:
    registry = ToolRegistry()
    handler = StubHandler(status=ExecutionStatus.SUCCESS)
    registry.register(get_spec("write_file"), handler)

    executor = RegistryToolExecutor(registry, pending_store)

    with pytest.raises(ToolResultContractError):
        await executor.execute(
            make_request(
                "write_file",
                arguments={
                    "path": "result.txt",
                    "content": "content",
                },
                request_id="request-fake-success",
            ),
            checkpoint_id="checkpoint-fake-success",
        )


@pytest.mark.asyncio
async def test_approved_handler_path_is_used(
    pending_store: PendingStore,
) -> None:
    registry = ToolRegistry()
    handler = ApprovalAwareStubHandler()
    registry.register(get_spec("read_file"), handler)

    executor = RegistryToolExecutor(registry, pending_store)
    request = make_request(
        "read_file",
        arguments={"path": ".env"},
        request_id="request-approved",
    )
    decision = make_approval_decision(
        request,
        status=ApprovalStatus.GRANTED,
    )

    result = await executor.execute(
        request,
        approval_decision=decision,
    )

    assert handler.approved_call_count == 1
    assert handler.call_count == 0
    assert result.output["approved"] is True


@pytest.mark.asyncio
async def test_normal_handler_path_is_used_without_approval(
    pending_store: PendingStore,
) -> None:
    registry = ToolRegistry()
    handler = ApprovalAwareStubHandler()
    registry.register(get_spec("read_file"), handler)

    executor = RegistryToolExecutor(registry, pending_store)
    request = make_request(
        "read_file",
        arguments={"path": "README.md"},
        request_id="request-normal",
    )

    result = await executor.execute(request)

    assert handler.call_count == 1
    assert handler.approved_call_count == 0
    assert result.output["handler"] is True


@pytest.mark.asyncio
async def test_denied_approval_is_rejected(
    pending_store: PendingStore,
) -> None:
    registry = ToolRegistry()
    handler = ApprovalAwareStubHandler()
    registry.register(get_spec("read_file"), handler)

    executor = RegistryToolExecutor(registry, pending_store)
    request = make_request(
        "read_file",
        arguments={"path": ".env"},
        request_id="request-denied",
    )
    decision = make_approval_decision(
        request,
        status=ApprovalStatus.DENIED,
    )

    with pytest.raises(ApprovalContextError):
        await executor.execute(
            request,
            approval_decision=decision,
        )

    assert handler.call_count == 0
    assert handler.approved_call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("task_id", "other-task"),
        ("step_id", "other-step"),
        ("request_id", "other-request"),
    ],
)
async def test_mismatched_approval_is_rejected(
    pending_store: PendingStore,
    field: str,
    replacement: str,
) -> None:
    registry = ToolRegistry()
    handler = ApprovalAwareStubHandler()
    registry.register(get_spec("read_file"), handler)

    executor = RegistryToolExecutor(registry, pending_store)
    request = make_request(
        "read_file",
        arguments={"path": ".env"},
        request_id="request-mismatch",
    )

    overrides: dict[str, str] = {field: replacement}
    decision = make_approval_decision(
        request,
        status=ApprovalStatus.GRANTED,
        task_id=overrides.get("task_id"),
        step_id=overrides.get("step_id"),
        request_id=overrides.get("request_id"),
    )

    with pytest.raises(ApprovalContextError):
        await executor.execute(
            request,
            approval_decision=decision,
        )

    assert handler.call_count == 0
    assert handler.approved_call_count == 0


@pytest.mark.asyncio
async def test_memory_write_uses_memory_store_without_filesystem_checkpoint(
    tmp_path: Path,
    pending_store: PendingStore,
) -> None:
    memory_store = FilesystemMemoryStore(tmp_path / "memory", max_value_bytes=4096)
    registry = ToolRegistry()
    registry.register(get_spec("memory_write"), MemoryWriteHandler(memory_store))
    executor = RegistryToolExecutor(registry, pending_store, memory_store)
    request = make_request(
        "memory_write",
        arguments={"key": "project.note", "value": {"text": "pending"}},
        request_id="request-memory-executor",
    )

    result = await executor.execute(request)

    assert result.status is ExecutionStatus.PENDING_COMMIT
    assert result.checkpoint_id is None
    assert [artifact["artifact_type"] for artifact in result.artifacts] == [
        "tool_output",
        "pending_memory",
    ]
    record = await memory_store.get(request.request_id)
    assert record.status is MemoryStatus.PENDING
    assert not (pending_store.pending_root / request.request_id).exists()


@pytest.mark.asyncio
async def test_memory_write_requires_configured_memory_store_before_handler_runs(
    tmp_path: Path,
    pending_store: PendingStore,
) -> None:
    handler_store = FilesystemMemoryStore(
        tmp_path / "handler-memory", max_value_bytes=4096
    )
    registry = ToolRegistry()
    registry.register(get_spec("memory_write"), MemoryWriteHandler(handler_store))
    executor = RegistryToolExecutor(registry, pending_store)
    request = make_request(
        "memory_write",
        arguments={"key": "project.note", "value": "pending"},
        request_id="request-memory-no-store",
    )

    with pytest.raises(PendingResourceUnavailableError):
        await executor.execute(request)

    assert not (handler_store.records_root / request.request_id).exists()


class TamperedMemoryArtifactHandler:
    def __init__(self, store: FilesystemMemoryStore) -> None:
        self._handler = MemoryWriteHandler(store)

    async def __call__(self, request: ToolCallRequest) -> ToolExecutionResult:
        result = await self._handler(request)
        artifacts = [dict(artifact) for artifact in result.artifacts]
        artifacts[1]["sha256"] = "0" * 64
        return result.model_copy(update={"artifacts": artifacts})


@pytest.mark.asyncio
async def test_invalid_memory_artifact_rolls_back_staged_memory(
    tmp_path: Path,
    pending_store: PendingStore,
) -> None:
    memory_store = FilesystemMemoryStore(tmp_path / "memory", max_value_bytes=4096)
    registry = ToolRegistry()
    registry.register(
        get_spec("memory_write"),
        TamperedMemoryArtifactHandler(memory_store),
    )
    executor = RegistryToolExecutor(registry, pending_store, memory_store)
    request = make_request(
        "memory_write",
        arguments={"key": "project.note", "value": "pending"},
        request_id="request-memory-invalid-artifact",
    )

    with pytest.raises(ToolResultContractError, match="pending_memory artifact"):
        await executor.execute(request)

    record = await memory_store.get(request.request_id)
    assert record.status is MemoryStatus.ROLLED_BACK
    assert await memory_store.get_trusted("project.note") is None


@pytest.mark.asyncio
async def test_repeated_pending_memory_write_is_idempotent(
    tmp_path: Path,
    pending_store: PendingStore,
) -> None:
    memory_store = FilesystemMemoryStore(tmp_path / "memory", max_value_bytes=4096)
    registry = ToolRegistry()
    registry.register(get_spec("memory_write"), MemoryWriteHandler(memory_store))
    executor = RegistryToolExecutor(registry, pending_store, memory_store)
    request = make_request(
        "memory_write",
        arguments={"key": "project.note", "value": {"text": "same"}},
        request_id="request-memory-idempotent",
    )

    first = await executor.execute(request)
    second = await executor.execute(request)

    assert first.pending_changes == second.pending_changes
    assert first.artifacts == second.artifacts
    assert len(list(memory_store.records_root.iterdir())) == 1


class StubQuarantineHandler:
    def __init__(
        self, store: FilesystemQuarantineStore, *, tamper: bool = False
    ) -> None:
        self.store = store
        self.tamper = tamper
        self.call_count = 0

    async def __call__(self, request: ToolCallRequest) -> ToolExecutionResult:
        from hashlib import sha256

        self.call_count += 1
        payload = b"downloaded"
        temporary = self.store.create_temporary_path(request.request_id)
        temporary.write_bytes(payload)
        record = await self.store.stage(
            request,
            source_url=str(request.arguments["url"]),
            final_url=str(request.arguments["url"]),
            redirect_chain=(),
            temporary_path=temporary,
            content_type="text/plain",
            content_sha256=sha256(payload).hexdigest(),
            size_bytes=len(payload),
            http_status=200,
        )
        output = {
            "downloaded": True,
            "source_url": record.source_url,
            "final_url": record.final_url,
            "content_type": record.content_type,
            "size_bytes": record.size_bytes,
            "sha256": record.content_sha256,
            "status": record.status.value,
        }
        quarantine_artifact = build_quarantined_download_artifact(
            request,
            quarantine_path=record.quarantine_path,
            source_url=record.source_url,
            final_url=record.final_url,
            content_sha256=record.content_sha256,
            size_bytes=record.size_bytes,
            content_type=record.content_type,
        )
        if self.tamper:
            quarantine_artifact["sha256"] = "0" * 64
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.PENDING_COMMIT,
            output=output,
            artifacts=[
                build_tool_output_artifact(
                    request, output, status=ExecutionStatus.PENDING_COMMIT
                ),
                quarantine_artifact,
            ],
            pending_changes=[
                {
                    "operation": "DOWNLOAD",
                    "quarantine_path": record.quarantine_path,
                    "source_url": record.source_url,
                    "final_url": record.final_url,
                    "content_sha256": record.content_sha256,
                    "size_bytes": record.size_bytes,
                    "status": record.status.value,
                }
            ],
        )


@pytest.mark.asyncio
async def test_download_uses_quarantine_without_filesystem_checkpoint(
    tmp_path: Path,
    pending_store: PendingStore,
) -> None:
    quarantine_store = FilesystemQuarantineStore(
        tmp_path / "quarantine", max_download_bytes=4096
    )
    registry = ToolRegistry()
    handler = StubQuarantineHandler(quarantine_store)
    registry.register(get_spec("download_url"), handler)
    executor = RegistryToolExecutor(
        registry, pending_store, quarantine_store=quarantine_store
    )
    request = make_request(
        "download_url",
        arguments={"url": "https://example.com/file"},
        request_id="request-download-executor",
    )

    result = await executor.execute(request)

    assert result.status is ExecutionStatus.PENDING_COMMIT
    assert result.checkpoint_id is None
    assert (await quarantine_store.get(request.request_id)).status is (
        QuarantineStatus.QUARANTINED
    )
    assert not (pending_store.pending_root / request.request_id).exists()


@pytest.mark.asyncio
async def test_download_requires_quarantine_store_before_handler_runs(
    tmp_path: Path,
    pending_store: PendingStore,
) -> None:
    handler_store = FilesystemQuarantineStore(
        tmp_path / "handler-quarantine", max_download_bytes=4096
    )
    registry = ToolRegistry()
    handler = StubQuarantineHandler(handler_store)
    registry.register(get_spec("download_url"), handler)
    executor = RegistryToolExecutor(registry, pending_store)
    request = make_request(
        "download_url",
        arguments={"url": "https://example.com/file"},
        request_id="request-download-no-store",
    )

    with pytest.raises(PendingResourceUnavailableError):
        await executor.execute(request)

    assert handler.call_count == 0


@pytest.mark.asyncio
async def test_invalid_download_artifact_rolls_back_quarantine(
    tmp_path: Path,
    pending_store: PendingStore,
) -> None:
    quarantine_store = FilesystemQuarantineStore(
        tmp_path / "quarantine", max_download_bytes=4096
    )
    registry = ToolRegistry()
    registry.register(
        get_spec("download_url"),
        StubQuarantineHandler(quarantine_store, tamper=True),
    )
    executor = RegistryToolExecutor(
        registry, pending_store, quarantine_store=quarantine_store
    )
    request = make_request(
        "download_url",
        arguments={"url": "https://example.com/file"},
        request_id="request-download-invalid-artifact",
    )

    with pytest.raises(ToolResultContractError, match="quarantined_download artifact"):
        await executor.execute(request)

    record = await quarantine_store.get(request.request_id)
    assert record.status is QuarantineStatus.ROLLED_BACK
