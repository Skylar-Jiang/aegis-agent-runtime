from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from ra_agent.contracts import ExecutionStatus, SourceType, ToolCallRequest
from ra_agent.execution.artifacts import validate_execution_artifacts
from ra_agent.execution.process_runner import RestrictedProcessRunner
from ra_agent.tools.implementations.run_shell import RestrictedShellHandler
from ra_agent.tools.path_resolver import SafePathResolver
from ra_agent.tools.shell_policy import ExecutableProfile, RestrictedShellPolicy


def make_request(command: str, *, cwd: str = ".") -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-shell",
        step_id="step-shell",
        request_id="request-shell",
        tool_name="run_shell",
        arguments={"command": command, "cwd": cwd},
        objective="run a restricted diagnostic process",
        context_summary="restricted shell unit test",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
    )


def make_handler(workspace: Path) -> RestrictedShellHandler:
    executable = Path(sys.executable).resolve()
    resolver = SafePathResolver(
        workspace,
        max_path_length=4096,
        max_read_bytes=1024 * 1024,
        max_write_bytes=1024 * 1024,
    )
    policy = RestrictedShellPolicy(
        resolver,
        {
            "helper": ExecutableProfile(
                name="helper",
                candidates=(executable.name,),
                validator="test",
            )
        },
        trusted_executable_roots=(executable.parent,),
        environment_source={},
    )
    return RestrictedShellHandler(policy, RestrictedProcessRunner())


@pytest.mark.asyncio
async def test_handler_returns_complete_inspectable_artifacts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "helper.py").write_text(
        "import sys\nprint('ok')\nprint('note', file=sys.stderr)\n",
        encoding="utf-8",
    )
    request = make_request("helper helper.py")

    result = await make_handler(workspace)(request)

    assert result.status is ExecutionStatus.SUCCESS
    assert result.output["executable"] == "helper"
    assert result.output["arguments"] == ["helper.py"]
    assert result.output["cwd"] == "."
    assert result.output["stdout"] == "ok\n"
    assert result.output["stderr"] == "note\n"
    assert [item["artifact_type"] for item in result.artifacts] == [
        "tool_output",
        "shell_execution",
    ]
    shell_artifact = result.artifacts[1]
    assert shell_artifact["timed_out"] is False
    assert shell_artifact["output_truncated"] is False
    assert str(Path(sys.executable).resolve()) not in str(shell_artifact)
    validate_execution_artifacts(request, result)


@pytest.mark.asyncio
async def test_nonzero_exit_is_failed_but_remains_inspectable(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "helper.py").write_text("raise SystemExit(7)\n", encoding="utf-8")
    request = make_request("helper helper.py")

    result = await make_handler(workspace)(request)

    assert result.status is ExecutionStatus.FAILED
    assert result.error_code == "NON_ZERO_EXIT"
    assert result.output["exit_code"] == 7
    validate_execution_artifacts(request, result)
