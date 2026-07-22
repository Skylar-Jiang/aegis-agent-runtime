from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from ra_agent.contracts import (
    ApprovalDecision,
    ApprovalStatus,
    ExecutionStatus,
    ToolCallRequest,
    ToolExecutionResult,
    ToolSpec,
)
from ra_agent.tools.registry import (
    ToolHandler,
    ToolRegistry,
)

from .artifacts import ArtifactContractError, validate_execution_artifacts
from .pending_store import PendingStore


class ToolExecutorError(RuntimeError):
    """Base error raised by the real tool executor."""


class UnknownToolError(ToolExecutorError):
    """Raised when the requested tool has no registered specification."""


class MissingToolHandlerError(ToolExecutorError):
    """Raised when a tool specification has no registered handler."""


class ToolDisabledError(ToolExecutorError):
    """Raised when execution is disabled by ToolSpec."""


class InvalidToolSpecError(ToolExecutorError):
    """Raised when a registered ToolSpec is invalid at execution time."""


class CheckpointRequiredError(ToolExecutorError):
    """Raised when a pending tool is executed without a checkpoint."""


class ToolResultContractError(ToolExecutorError):
    """Raised when a handler returns an invalid execution result."""


class ToolExecutionTimeoutError(ToolExecutorError):
    """Raised when a handler exceeds its configured timeout."""


class ApprovalContextError(ToolExecutorError):
    """Raised when an approval decision does not match the request."""


class ToolExecutor(Protocol):
    async def execute(
        self,
        request: ToolCallRequest,
        *,
        checkpoint_id: str | None = None,
        approval_decision: ApprovalDecision | None = None,
    ) -> ToolExecutionResult: ...


@runtime_checkable
class ApprovalAwareToolHandler(Protocol):
    """
    Optional internal extension for handlers that support an approved path.

    The public ToolHandler contract remains unchanged.
    """

    async def execute_approved(
        self,
        request: ToolCallRequest,
        approval_decision: ApprovalDecision,
    ) -> ToolExecutionResult: ...


class RegistryToolExecutor:
    """Execute registered handlers under shared runtime safeguards."""

    def __init__(
        self,
        registry: ToolRegistry,
        pending_store: PendingStore,
    ) -> None:
        self._registry = registry
        self._pending_store = pending_store

    async def execute(
        self,
        request: ToolCallRequest,
        *,
        checkpoint_id: str | None = None,
        approval_decision: ApprovalDecision | None = None,
    ) -> ToolExecutionResult:
        spec = self._get_spec(request.tool_name)
        handler = self._get_handler(request.tool_name)

        self._validate_spec(spec)
        self._validate_checkpoint(
            spec,
            checkpoint_id,
        )
        self._validate_approval(
            request,
            approval_decision,
        )

        started_at = datetime.now(UTC)

        try:
            async with asyncio.timeout(spec.timeout_seconds):
                result = await self._invoke_handler(
                    handler,
                    request,
                    approval_decision,
                )
        except TimeoutError as error:
            raise ToolExecutionTimeoutError(
                f"tool {request.tool_name!r} exceeded its {spec.timeout_seconds}-second timeout"
            ) from error

        if not isinstance(result, ToolExecutionResult):
            raise ToolResultContractError("tool handler did not return ToolExecutionResult")

        self._validate_result(
            spec,
            result,
            checkpoint_id,
        )

        if result.status is ExecutionStatus.PENDING_COMMIT:
            if checkpoint_id is None:
                raise CheckpointRequiredError("PENDING_COMMIT result requires a checkpoint")

            record = await self._pending_store.bind_checkpoint(
                request.request_id,
                checkpoint_id,
            )

            if record.request_id != request.request_id:
                raise ToolResultContractError("pending record request_id does not match request")

            if record.tool_name != request.tool_name:
                raise ToolResultContractError("pending record tool_name does not match request")

            if not await self._pending_store.verify_integrity(request.request_id):
                raise ToolResultContractError("pending record failed integrity verification")

        try:
            validate_execution_artifacts(request, result)
        except ArtifactContractError as error:
            raise ToolResultContractError(f"invalid execution artifacts: {error}") from error

        finished_at = datetime.now(UTC)

        # Executor 是关联字段和执行时间的可信来源。
        return result.model_copy(
            update={
                "task_id": request.task_id,
                "step_id": request.step_id,
                "request_id": request.request_id,
                "checkpoint_id": checkpoint_id,
                "started_at": started_at,
                "finished_at": finished_at,
            }
        )

    def _get_spec(
        self,
        tool_name: str,
    ) -> ToolSpec:
        try:
            return self._registry.get_spec(tool_name)
        except KeyError as error:
            raise UnknownToolError(f"unknown tool: {tool_name}") from error

    def _get_handler(
        self,
        tool_name: str,
    ) -> ToolHandler:
        try:
            return self._registry.get_handler(tool_name)
        except KeyError as error:
            raise UnknownToolError(f"unknown tool: {tool_name}") from error
        except LookupError as error:
            raise MissingToolHandlerError(f"tool has no registered handler: {tool_name}") from error

    @staticmethod
    def _validate_spec(
        spec: ToolSpec,
    ) -> None:
        if spec.timeout_seconds <= 0:
            raise InvalidToolSpecError(f"tool {spec.name!r} has an invalid timeout")

        sandbox_mode = spec.sandbox_mode.upper()

        if sandbox_mode.startswith("DISABLED_"):
            raise ToolDisabledError(f"tool {spec.name!r} is disabled: {spec.sandbox_mode}")

    @staticmethod
    def _validate_checkpoint(
        spec: ToolSpec,
        checkpoint_id: str | None,
    ) -> None:
        if checkpoint_id is not None:
            if not isinstance(checkpoint_id, str):
                raise TypeError("checkpoint_id must be a string or None")

            if not checkpoint_id or checkpoint_id.isspace():
                raise ValueError("checkpoint_id must not be empty")

        if spec.sandbox_mode.upper() == "PENDING" and checkpoint_id is None:
            raise CheckpointRequiredError(f"tool {spec.name!r} requires a checkpoint")

    @staticmethod
    def _validate_approval(
        request: ToolCallRequest,
        approval_decision: ApprovalDecision | None,
    ) -> None:
        if approval_decision is None:
            return

        if approval_decision.status is not ApprovalStatus.GRANTED:
            raise ApprovalContextError("approval decision is not granted")

        if approval_decision.task_id != request.task_id:
            raise ApprovalContextError("approval task_id does not match request")

        if approval_decision.step_id != request.step_id:
            raise ApprovalContextError("approval step_id does not match request")

        if approval_decision.request_id != request.request_id:
            raise ApprovalContextError("approval request_id does not match request")

    @staticmethod
    async def _invoke_handler(
        handler: ToolHandler,
        request: ToolCallRequest,
        approval_decision: ApprovalDecision | None,
    ) -> ToolExecutionResult:
        if approval_decision is not None and isinstance(
            handler,
            ApprovalAwareToolHandler,
        ):
            return await handler.execute_approved(
                request,
                approval_decision,
            )

        return await handler(request)

    @staticmethod
    def _validate_result(
        spec: ToolSpec,
        result: ToolExecutionResult,
        checkpoint_id: str | None,
    ) -> None:
        sandbox_mode = spec.sandbox_mode.upper()

        if result.status is ExecutionStatus.PENDING_COMMIT:
            if sandbox_mode != "PENDING":
                raise ToolResultContractError("non-pending tool returned PENDING_COMMIT")

            if checkpoint_id is None:
                raise CheckpointRequiredError("PENDING_COMMIT result requires a checkpoint")

            if not result.pending_changes:
                raise ToolResultContractError("PENDING_COMMIT result has no pending changes")

        if sandbox_mode == "PENDING" and result.status is ExecutionStatus.SUCCESS:
            raise ToolResultContractError("pending tool returned SUCCESS instead of PENDING_COMMIT")


class MockToolExecutor:
    """Returns mock output and never invokes a real tool."""

    async def execute(
        self,
        request: ToolCallRequest,
        *,
        checkpoint_id: str | None = None,
        approval_decision: ApprovalDecision | None = None,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=(
                ExecutionStatus.PENDING_COMMIT
                if checkpoint_id is not None
                else ExecutionStatus.SUCCESS
            ),
            checkpoint_id=checkpoint_id,
            output={"mock": True, "tool_name": request.tool_name},
        )
