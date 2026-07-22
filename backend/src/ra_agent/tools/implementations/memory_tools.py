from __future__ import annotations

import asyncio
from typing import Any

from ra_agent.contracts import ExecutionStatus, MemoryStatus, ToolCallRequest, ToolExecutionResult
from ra_agent.execution.artifacts import (
    build_pending_memory_artifact,
    build_tool_output_artifact,
)
from ra_agent.memory import FilesystemMemoryStore, MemoryRecord


class MemoryToolError(ValueError):
    """Base validation error raised by trusted-memory tool handlers."""


class MemoryWriteHandler:
    """Stage untrusted memory for PostCheck without making it agent-readable."""

    TOOL_NAME = "memory_write"

    def __init__(self, store: FilesystemMemoryStore) -> None:
        self._store = store

    async def __call__(self, request: ToolCallRequest) -> ToolExecutionResult:
        if request.tool_name != self.TOOL_NAME:
            raise MemoryToolError(f"handler only supports {self.TOOL_NAME}")

        key = request.arguments.get("key")
        if not isinstance(key, str):
            raise MemoryToolError("memory_write argument 'key' must be a string")
        if "value" not in request.arguments:
            raise MemoryToolError("memory_write argument 'value' is required")
        value = request.arguments["value"]

        record = await self._stage_cancellation_safe(request, key=key, value=value)
        if record.status is not MemoryStatus.PENDING:
            raise MemoryToolError(
                f"memory_write request_id already reached terminal state: {record.status.value}"
            )

        try:
            output: dict[str, Any] = {
                "staged": True,
                "memory_id": record.memory_id,
                "key": record.key,
                "status": record.status.value,
                "size_bytes": record.size_bytes,
            }
            pending_change: dict[str, Any] = {
                "operation": "MEMORY_WRITE",
                "memory_id": record.memory_id,
                "key": record.key,
                "content_sha256": record.content_sha256,
                "size_bytes": record.size_bytes,
                "status": record.status.value,
            }
            artifacts = [
                build_tool_output_artifact(
                    request,
                    output,
                    status=ExecutionStatus.PENDING_COMMIT,
                ),
                build_pending_memory_artifact(
                    request,
                    memory_id=record.memory_id,
                    key=record.key,
                    path=record.payload_path,
                    content_sha256=record.content_sha256,
                    size_bytes=record.size_bytes,
                ),
            ]
        except Exception:
            await self._rollback_if_pending(
                record,
                reason="memory_write result construction failed",
            )
            raise

        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.PENDING_COMMIT,
            output=output,
            artifacts=artifacts,
            pending_changes=[pending_change],
        )

    async def _stage_cancellation_safe(
        self,
        request: ToolCallRequest,
        *,
        key: str,
        value: object,
    ) -> MemoryRecord:
        stage_task = asyncio.create_task(
            self._store.stage(
                request,
                key=key,
                value=value,
            )
        )
        try:
            return await asyncio.shield(stage_task)
        except asyncio.CancelledError:
            try:
                record = await stage_task
            except Exception:
                raise
            await asyncio.shield(
                self._rollback_if_pending(
                    record,
                    reason="memory_write cancelled",
                )
            )
            raise

    async def _rollback_if_pending(self, record: MemoryRecord, *, reason: str) -> None:
        current = await self._store.get(record.request_id)
        if current.status is MemoryStatus.PENDING:
            await self._store.mark_rolled_back(record.request_id, reason=reason)


class MemoryReadHandler:
    """Read only the latest TRUSTED version for a memory key."""

    TOOL_NAME = "memory_read"

    def __init__(self, store: FilesystemMemoryStore) -> None:
        self._store = store

    async def __call__(self, request: ToolCallRequest) -> ToolExecutionResult:
        if request.tool_name != self.TOOL_NAME:
            raise MemoryToolError(f"handler only supports {self.TOOL_NAME}")

        key = request.arguments.get("key")
        if not isinstance(key, str):
            raise MemoryToolError("memory_read argument 'key' must be a string")

        trusted = await self._store.get_trusted_value(key)
        if trusted is None:
            output: dict[str, Any] = {
                "found": False,
                "key": key,
                "value": None,
            }
        else:
            record, value = trusted
            output = {
                "found": True,
                "memory_id": record.memory_id,
                "key": record.key,
                "value": value,
                "status": record.status.value,
            }

        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.SUCCESS,
            output=output,
            artifacts=[
                build_tool_output_artifact(
                    request,
                    output,
                    status=ExecutionStatus.SUCCESS,
                )
            ],
        )
