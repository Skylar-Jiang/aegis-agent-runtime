from __future__ import annotations

from ra_agent.contracts import (
    ExecutionStatus,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution.pending_store import PendingStore
from ra_agent.tools.path_resolver import SafePathResolver


class DeleteFileHandler:
    """Stage a file deletion without deleting the trusted file."""

    TOOL_NAME = "delete_file"

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

        target = self._path_resolver.resolve_delete_target(raw_path)
        target_path = self._path_resolver.to_relative(target)
        original_size_bytes = target.stat().st_size

        record = await self._pending_store.stage_delete(
            request.request_id,
            target_path,
        )

        pending_change = {
            "operation": record.operation.value,
            "target_path": record.target_path,
            "original_size_bytes": original_size_bytes,
            "status": record.status.value,
        }

        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.PENDING_COMMIT,
            output={
                "staged": True,
                "operation": record.operation.value,
                "target_path": record.target_path,
            },
            sandbox_path=request.request_id,
            pending_changes=[pending_change],
        )

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
            raise ValueError("delete_file argument 'path' must be a string")

        if not raw_path or raw_path.isspace():
            raise ValueError("delete_file argument 'path' must not be empty")

        return raw_path
