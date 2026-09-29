from __future__ import annotations

from typing import Any

from ra_agent.contracts import ExecutionStatus, ToolCallRequest, ToolExecutionResult
from ra_agent.execution.artifacts import (
    build_shell_execution_artifact,
    build_tool_output_artifact,
)
from ra_agent.execution.process_runner import RestrictedProcessRunner
from ra_agent.tools.shell_policy import RestrictedShellPolicy


class RestrictedShellToolError(ValueError):
    """Raised when run_shell is called outside its restricted contract."""


class RestrictedShellHandler:
    """Execute one allowlisted argv without a shell interpreter."""

    TOOL_NAME = "run_shell"

    def __init__(
        self,
        policy: RestrictedShellPolicy,
        runner: RestrictedProcessRunner,
    ) -> None:
        self._policy = policy
        self._runner = runner

    async def __call__(self, request: ToolCallRequest) -> ToolExecutionResult:
        if request.tool_name != self.TOOL_NAME:
            raise RestrictedShellToolError(f"handler only supports {self.TOOL_NAME}")

        validated = self._policy.validate_request(request.arguments)
        record = await self._runner.run(validated)
        status = ExecutionStatus.SUCCESS if record.exit_code == 0 else ExecutionStatus.FAILED
        output: dict[str, Any] = {
            "executable": record.executable,
            "arguments": list(record.arguments),
            "cwd": record.cwd,
            "exit_code": record.exit_code,
            "stdout": record.stdout,
            "stderr": record.stderr,
            "duration_ms": record.duration_ms,
            "environment_keys": list(record.environment_keys),
        }
        artifacts = [
            build_tool_output_artifact(request, output, status=status),
            build_shell_execution_artifact(
                request,
                command=str(request.arguments["command"]),
                record=record,
                status=status,
            ),
        ]
        return ToolExecutionResult(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            status=status,
            output=output,
            error=(
                None
                if status is ExecutionStatus.SUCCESS
                else f"restricted process exited with code {record.exit_code}"
            ),
            error_code=(None if status is ExecutionStatus.SUCCESS else "NON_ZERO_EXIT"),
            artifacts=artifacts,
        )
