from __future__ import annotations

import asyncio
from pathlib import PurePosixPath

from ra_agent.contracts import (
    ExecutionStatus,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution.artifacts import (
    build_pending_file_artifact,
    build_tool_output_artifact,
)
from ra_agent.execution.pending_store import PendingRecord, PendingStore
from ra_agent.tools.path_resolver import SafePathResolver


class WriteFileHandler:
    """Stage a UTF-8 file write without modifying the trusted workspace."""

    TOOL_NAME = "write_file"

    def __init__(
        self,
        path_resolver: SafePathResolver,
        pending_store: PendingStore,
    ) -> None:
        self._path_resolver = path_resolver
        self._pending_store = pending_store

    async def __call__(
        self,
        request: ToolCallRequest,
    ) -> ToolExecutionResult:
        self._validate_tool_name(request)

        raw_path = self._get_required_path(request)
        content = self._get_required_content(request)

        payload = content.encode("utf-8")
        size_bytes = self._path_resolver.validate_write_content(payload)

        target = self._path_resolver.resolve_write_target(raw_path)
        target_path = self._path_resolver.to_relative(target)

        record = await self._stage_write_cancellation_safe(
            request,
            target_path=target_path,
            payload=payload,
        )

        if (
            record.pending_path is None
            or record.content_sha256 is None
            or record.size_bytes is None
        ):
            raise RuntimeError("PendingStore returned an incomplete write record")

        pending_directory = PurePosixPath(record.pending_path).parent.as_posix()

        pending_change = {
            "operation": record.operation.value,
            "target_path": record.target_path,
            "pending_path": record.pending_path,
            "content_sha256": record.content_sha256,
            "size_bytes": record.size_bytes,
            "status": record.status.value,
        }
        output = {
            "staged": True,
            "operation": record.operation.value,
            "target_path": record.target_path,
            "size_bytes": size_bytes,
        }

        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.PENDING_COMMIT,
            output=output,
            sandbox_path=pending_directory,
            artifacts=[
                build_tool_output_artifact(
                    request,
                    output,
                    status=ExecutionStatus.PENDING_COMMIT,
                ),
                build_pending_file_artifact(request, record),
            ],
            pending_changes=[pending_change],
        )

    async def _stage_write_cancellation_safe(
        self,
        request: ToolCallRequest,
        *,
        target_path: str,
        payload: bytes,
    ) -> PendingRecord:
        stage_task = asyncio.create_task(
            self._pending_store.stage_write(
                request.request_id,
                target_path,
                payload,
            )
        )
        try:
            return await asyncio.shield(stage_task)
        except asyncio.CancelledError as cancellation:
            try:
                await stage_task
            except Exception as stage_error:
                cancellation.add_note(
                    f"pending write stage also failed: {type(stage_error).__name__}: {stage_error}"
                )
            try:
                await asyncio.shield(self._pending_store.cleanup(request.request_id))
            except Exception as cleanup_error:
                cancellation.add_note(
                    f"pending write cleanup failed: {type(cleanup_error).__name__}: {cleanup_error}"
                )
            raise

    def _validate_tool_name(
        self,
        request: ToolCallRequest,
    ) -> None:
        if request.tool_name != self.TOOL_NAME:
            raise ValueError(f"{type(self).__name__} cannot execute tool {request.tool_name!r}")

    @staticmethod
    def _get_required_path(
        request: ToolCallRequest,
    ) -> str:
        raw_path = request.arguments.get("path")

        if not isinstance(raw_path, str):
            raise ValueError("write_file argument 'path' must be a string")

        if not raw_path or raw_path.isspace():
            raise ValueError("write_file argument 'path' must not be empty")

        return raw_path

    @staticmethod
    def _get_required_content(
        request: ToolCallRequest,
    ) -> str:
        content = request.arguments.get("content")

        if not isinstance(content, str):
            raise ValueError("write_file argument 'content' must be a string")

        return content
