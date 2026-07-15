from __future__ import annotations

import asyncio
from hashlib import sha256

from ra_agent.contracts import ExecutionStatus, ToolCallRequest, ToolExecutionResult
from ra_agent.tools.path_resolver import SafePathResolver


class ReadFileHandler:
    """Safely read one UTF-8 regular file inside the workspace."""

    TOOL_NAME = "read_file"

    def __init__(
        self,
        path_resolver: SafePathResolver,
    ) -> None:
        self._path_resolver = path_resolver

    async def __call__(
        self,
        request: ToolCallRequest,
    ) -> ToolExecutionResult:
        self._validate_tool_name(request)
        raw_path = self._get_required_path(request)

        resolved_path = self._path_resolver.resolve_existing_file(raw_path)
        payload = await asyncio.to_thread(resolved_path.read_bytes)

        # 读取完成后再检查一次，避免读取前后的文件大小变化。
        size_bytes = self._path_resolver.validate_read_content(payload)

        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(f"file is not valid UTF-8: {raw_path}") from error

        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.SUCCESS,
            output={
                "path": self._path_resolver.to_relative(resolved_path),
                "content": content,
                "size_bytes": size_bytes,
                "sha256": sha256(payload).hexdigest(),
                "encoding": "utf-8",
            },
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
            raise ValueError("read_file argument 'path' must be a string")

        if not raw_path or raw_path.isspace():
            raise ValueError("read_file argument 'path' must not be empty")

        return raw_path
