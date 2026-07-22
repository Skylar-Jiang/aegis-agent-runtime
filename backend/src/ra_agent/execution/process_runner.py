from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import yaml

from ra_agent.tools.shell_policy import ValidatedProcessRequest


class RestrictedProcessError(RuntimeError):
    """Base error raised by restricted child process execution."""


class RestrictedProcessTimeoutError(RestrictedProcessError):
    """Raised when the child process exceeds its internal deadline."""


class ProcessOutputLimitError(RestrictedProcessError):
    """Raised when stdout, stderr, or their combined size exceeds policy."""


@dataclass(frozen=True, slots=True)
class ProcessExecutionRecord:
    executable: str
    arguments: tuple[str, ...]
    cwd: str
    exit_code: int
    stdout: str
    stderr: str
    stdout_sha256: str
    stdout_size_bytes: int
    stderr_sha256: str
    stderr_size_bytes: int
    duration_ms: int
    environment_keys: tuple[str, ...]


@dataclass(slots=True)
class _OutputBudget:
    combined_limit: int
    consumed: int = 0

    def add(self, amount: int) -> None:
        self.consumed += amount
        if self.consumed > self.combined_limit:
            raise ProcessOutputLimitError("combined process output exceeds configured limit")


class RestrictedProcessRunner:
    """Run one validated argv without invoking any system shell."""

    def __init__(
        self,
        *,
        max_stdout_bytes: int = 1_048_576,
        max_stderr_bytes: int = 1_048_576,
        max_combined_output_bytes: int = 2_097_152,
        terminate_grace_seconds: float = 1.0,
        read_chunk_bytes: int = 65_536,
    ) -> None:
        for name, value in (
            ("max_stdout_bytes", max_stdout_bytes),
            ("max_stderr_bytes", max_stderr_bytes),
            ("max_combined_output_bytes", max_combined_output_bytes),
            ("read_chunk_bytes", read_chunk_bytes),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if terminate_grace_seconds <= 0:
            raise ValueError("terminate_grace_seconds must be positive")

        self._max_stdout_bytes = max_stdout_bytes
        self._max_stderr_bytes = max_stderr_bytes
        self._max_combined_output_bytes = max_combined_output_bytes
        self._terminate_grace_seconds = terminate_grace_seconds
        self._read_chunk_bytes = read_chunk_bytes

    @classmethod
    def from_yaml(cls, config_path: Path) -> RestrictedProcessRunner:
        with config_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
        if not isinstance(raw, dict):
            raise ValueError("tool policy document must be a mapping")
        section = raw.get("restricted_shell")
        if not isinstance(section, dict) or section.get("enabled") is not True:
            raise ValueError("restricted_shell configuration is missing or disabled")
        return cls(
            max_stdout_bytes=cls._positive_int(
                section.get("max_stdout_bytes"),
                "max_stdout_bytes",
            ),
            max_stderr_bytes=cls._positive_int(
                section.get("max_stderr_bytes"),
                "max_stderr_bytes",
            ),
            max_combined_output_bytes=cls._positive_int(
                section.get("max_combined_output_bytes"),
                "max_combined_output_bytes",
            ),
            terminate_grace_seconds=cls._positive_number(
                section.get("terminate_grace_seconds"),
                "terminate_grace_seconds",
            ),
        )

    async def run(self, validated: ValidatedProcessRequest) -> ProcessExecutionRecord:
        started = time.monotonic()
        process: asyncio.subprocess.Process | None = None
        stdout_bytes = b""
        stderr_bytes = b""
        exit_code = -1
        stdout_task: asyncio.Task[bytes] | None = None
        stderr_task: asyncio.Task[bytes] | None = None
        wait_task: asyncio.Task[int] | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                str(validated.executable_path),
                *validated.arguments,
                cwd=str(validated.cwd),
                env=dict(validated.environment),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if process.stdout is None or process.stderr is None:
                raise RestrictedProcessError("child process pipes were not created")

            budget = _OutputBudget(self._max_combined_output_bytes)
            stdout_task = asyncio.create_task(
                self._read_limited(
                    process.stdout,
                    limit=self._max_stdout_bytes,
                    stream_name="stdout",
                    budget=budget,
                )
            )
            stderr_task = asyncio.create_task(
                self._read_limited(
                    process.stderr,
                    limit=self._max_stderr_bytes,
                    stream_name="stderr",
                    budget=budget,
                )
            )
            wait_task = asyncio.create_task(process.wait())

            try:
                async with asyncio.timeout(validated.timeout_seconds):
                    stdout_bytes, stderr_bytes, exit_code = await asyncio.gather(
                        stdout_task,
                        stderr_task,
                        wait_task,
                    )
            except TimeoutError as error:
                await self._terminate_shielded(process)
                raise RestrictedProcessTimeoutError(
                    f"restricted process exceeded {validated.timeout_seconds:g} seconds"
                ) from error
        except asyncio.CancelledError:
            if process is not None:
                await self._terminate_shielded(process)
            raise
        except Exception:
            if process is not None and process.returncode is None:
                await self._terminate_shielded(process)
            raise
        finally:
            for task in (stdout_task, stderr_task, wait_task):
                if task is None:
                    continue
                if not task.done():
                    task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        stdout = self._normalize_newlines(stdout_bytes.decode("utf-8", errors="replace"))
        stderr = self._normalize_newlines(stderr_bytes.decode("utf-8", errors="replace"))
        visible_stdout = stdout.encode("utf-8")
        visible_stderr = stderr.encode("utf-8")
        return ProcessExecutionRecord(
            executable=validated.executable_name,
            arguments=validated.arguments,
            cwd=validated.cwd_relative,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            stdout_sha256=sha256(visible_stdout).hexdigest(),
            stdout_size_bytes=len(visible_stdout),
            stderr_sha256=sha256(visible_stderr).hexdigest(),
            stderr_size_bytes=len(visible_stderr),
            duration_ms=duration_ms,
            environment_keys=tuple(sorted(validated.environment)),
        )

    async def _read_limited(
        self,
        stream: asyncio.StreamReader,
        *,
        limit: int,
        stream_name: str,
        budget: _OutputBudget,
    ) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await stream.read(self._read_chunk_bytes)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ProcessOutputLimitError(f"{stream_name} exceeds configured output limit")
            budget.add(len(chunk))
            chunks.append(chunk)
        return b"".join(chunks)

    async def _terminate_shielded(self, process: asyncio.subprocess.Process) -> None:
        termination = asyncio.create_task(self._terminate(process))
        try:
            await asyncio.shield(termination)
        except asyncio.CancelledError:
            await termination

    async def _terminate(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=self._terminate_grace_seconds,
            )
        except TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                return
            await process.wait()

    @staticmethod
    def _normalize_newlines(value: str) -> str:
        """Normalize child-process text output for cross-platform artifacts."""
        return value.replace("\r\n", "\n").replace("\r", "\n")

    @staticmethod
    def _positive_int(value: object, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{label} must be a positive integer")
        return value

    @staticmethod
    def _positive_number(value: object, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
            raise ValueError(f"{label} must be positive")
        return float(value)
