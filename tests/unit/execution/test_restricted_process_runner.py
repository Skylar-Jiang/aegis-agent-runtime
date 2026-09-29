from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from ra_agent.execution.process_runner import (
    ProcessOutputLimitError,
    RestrictedProcessRunner,
    RestrictedProcessTimeoutError,
)
from ra_agent.tools.shell_policy import ValidatedProcessRequest


def make_script(workspace: Path, body: str) -> Path:
    script = workspace / "helper.py"
    script.write_text(body, encoding="utf-8")
    return script


def validated(
    workspace: Path,
    script: Path,
    *arguments: str,
    timeout: float = 2.0,
) -> ValidatedProcessRequest:
    executable = Path(sys.executable).resolve()
    return ValidatedProcessRequest(
        executable_name="helper",
        executable_path=executable,
        arguments=(script.name, *arguments),
        cwd=workspace.resolve(),
        cwd_relative=".",
        environment={"PATH": str(executable.parent)},
        timeout_seconds=timeout,
    )


@pytest.mark.asyncio
async def test_runner_captures_stdout_stderr_and_exit_code(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = make_script(
        workspace,
        "import sys\nprint('hello')\nprint('warning', file=sys.stderr)\nsys.exit(3)\n",
    )
    runner = RestrictedProcessRunner()

    record = await runner.run(validated(workspace, script))

    assert record.exit_code == 3
    assert record.stdout == "hello\n"
    assert record.stderr == "warning\n"
    assert record.stdout_size_bytes == len(record.stdout.encode())
    assert record.stderr_size_bytes == len(record.stderr.encode())
    assert record.executable == "helper"
    assert record.cwd == "."


@pytest.mark.asyncio
async def test_runner_rejects_stdout_overflow_and_reaps_process(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = make_script(workspace, "print('x' * 10000)\n")
    runner = RestrictedProcessRunner(
        max_stdout_bytes=100,
        max_stderr_bytes=100,
        max_combined_output_bytes=150,
    )

    with pytest.raises(ProcessOutputLimitError):
        await runner.run(validated(workspace, script))


@pytest.mark.asyncio
async def test_runner_times_out_and_terminates_process(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = make_script(workspace, "import time\ntime.sleep(30)\n")
    runner = RestrictedProcessRunner(terminate_grace_seconds=0.1)

    with pytest.raises(RestrictedProcessTimeoutError):
        await runner.run(validated(workspace, script, timeout=0.05))


@pytest.mark.asyncio
async def test_runner_cancellation_terminates_process(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = make_script(workspace, "import time\ntime.sleep(30)\n")
    runner = RestrictedProcessRunner(terminate_grace_seconds=0.1)
    task = asyncio.create_task(runner.run(validated(workspace, script, timeout=10)))

    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_runner_loads_output_limits_from_yaml(tmp_path: Path) -> None:
    config = tmp_path / "tool_policies.yaml"
    config.write_text(
        """
restricted_shell:
  enabled: true
  max_stdout_bytes: 100
  max_stderr_bytes: 200
  max_combined_output_bytes: 250
  terminate_grace_seconds: 0.1
""".strip(),
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = make_script(workspace, "print('x' * 150)\n")
    runner = RestrictedProcessRunner.from_yaml(config)

    with pytest.raises(ProcessOutputLimitError):
        await runner.run(validated(workspace, script))
