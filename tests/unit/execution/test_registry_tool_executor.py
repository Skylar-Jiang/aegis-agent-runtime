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
    SourceType,
    ToolCallRequest,
    ToolExecutionResult,
    ToolSpec,
)
from ra_agent.execution.artifacts import build_tool_output_artifact
from ra_agent.execution.executor import (
    ApprovalContextError,
    CheckpointRequiredError,
    InvalidToolSpecError,
    MissingToolHandlerError,
    RegistryToolExecutor,
    ToolDisabledError,
    ToolExecutionTimeoutError,
    ToolResultContractError,
    UnknownToolError,
)
from ra_agent.execution.pending_store import PendingConflictError, PendingStore
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
