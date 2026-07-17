from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ra_agent.contracts import ExecutionStatus, ToolCallRequest, ToolExecutionResult
from ra_agent.tools.path_resolver import SafePathResolver


class ListDirHandler:
    """Safely list regular files and directories inside the workspace."""

    TOOL_NAME = "list_dir"

    def __init__(
        self,
        path_resolver: SafePathResolver,
        *,
        max_entries: int,
    ) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")

        self._path_resolver = path_resolver
        self._max_entries = max_entries

    async def __call__(
        self,
        request: ToolCallRequest,
    ) -> ToolExecutionResult:
        self._validate_tool_name(request)
        raw_path = self._get_required_path(request)

        directory = self._path_resolver.resolve_existing_dir(raw_path)
        entries, truncated = await asyncio.to_thread(
            self._scan_directory,
            directory,
        )

        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=ExecutionStatus.SUCCESS,
            output={
                "path": self._path_resolver.to_relative(directory),
                "entries": entries,
                "returned_count": len(entries),
                "truncated": truncated,
            },
        )

    def _scan_directory(
        self,
        directory: Path,
    ) -> tuple[list[dict[str, Any]], bool]:
        candidates: list[Path] = []

        for entry in sorted(
            directory.iterdir(),
            key=lambda item: item.name.casefold(),
        ):
            # 第一版保守处理：不展示和跟随符号链接。
            if entry.is_symlink():
                continue
            if self._path_resolver.is_sensitive_path(entry):
                continue

            if entry.is_file() or entry.is_dir():
                candidates.append(entry)

        truncated = len(candidates) > self._max_entries
        selected = candidates[: self._max_entries]

        entries: list[dict[str, Any]] = []

        for entry in selected:
            if entry.is_dir():
                entry_type = "directory"
                size_bytes: int | None = None
            else:
                entry_type = "file"
                size_bytes = entry.stat().st_size

            item: dict[str, Any] = {
                "name": entry.name,
                "path": self._path_resolver.to_relative(entry),
                "type": entry_type,
            }

            if size_bytes is not None:
                item["size_bytes"] = size_bytes

            entries.append(item)

        return entries, truncated

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
            raise ValueError("list_dir argument 'path' must be a string")

        if not raw_path or raw_path.isspace():
            raise ValueError("list_dir argument 'path' must not be empty")

        return raw_path
