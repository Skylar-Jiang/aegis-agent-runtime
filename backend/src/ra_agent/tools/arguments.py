from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ra_agent.contracts import DataLineage


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathArguments(ToolArguments):
    path: str = Field(min_length=1)


class WriteFileArguments(PathArguments):
    content: str


class DownloadUrlArguments(ToolArguments):
    url: str = Field(min_length=1)
    destination: str | None = Field(default=None, min_length=1)


class MemoryReadArguments(ToolArguments):
    key: str = Field(min_length=1)


class MemoryWriteArguments(MemoryReadArguments):
    value: Any


class SendEmailDryRunArguments(ToolArguments):
    artifact: DataLineage
    recipient: str = Field(min_length=1)


class RunShellArguments(ToolArguments):
    command: str = Field(min_length=1)
    cwd: str | None = None


TOOL_ARGUMENT_MODELS: dict[str, type[ToolArguments]] = {
    "list_dir": PathArguments,
    "read_file": PathArguments,
    "create_file": WriteFileArguments,
    "write_file": WriteFileArguments,
    "delete_file": PathArguments,
    "download_url": DownloadUrlArguments,
    "memory_read": MemoryReadArguments,
    "memory_write": MemoryWriteArguments,
    "send_email_dry_run": SendEmailDryRunArguments,
    "run_shell": RunShellArguments,
}


def input_schema(tool_name: str) -> dict[str, Any]:
    return TOOL_ARGUMENT_MODELS[tool_name].model_json_schema()
