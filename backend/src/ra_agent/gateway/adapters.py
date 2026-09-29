"""Real Core PR2 tool adapters behind the ToolGateway boundary.

These adapters intentionally cover the four Core acceptance categories only:
filesystem, Memory, simulated egress, and restricted process execution.  They do not
open an alternate HTTP route and require the opaque capability installed by
ToolGateway, preventing accidental in-process bypass of the gateway boundary.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Any

from ra_agent.contracts.core_v1 import ToolCallEnvelope
from ra_agent.execution.process_runner import RestrictedProcessRunner
from ra_agent.tools.path_resolver import PathResolutionError, SafePathResolver
from ra_agent.tools.shell_policy import RestrictedShellPolicy, RestrictedShellPolicyError


class AdapterBypassError(PermissionError):
    pass


class ToolExecutionRejected(ValueError):
    """A tool request was safely rejected before completing its side effect."""


class CoreToolExecutor:
    """Minimal real adapters used by Core v1 integration tests and demo runtime."""

    def __init__(
        self,
        *,
        path_resolver: SafePathResolver,
        memory_path: Path,
        outbox_path: Path,
        process_policy: RestrictedShellPolicy | None = None,
        process_runner: RestrictedProcessRunner | None = None,
        max_list_entries: int = 1000,
    ) -> None:
        if max_list_entries <= 0:
            raise ValueError("max_list_entries must be positive")
        self.max_list_entries = max_list_entries
        self.path_resolver = path_resolver
        self.memory_path = Path(memory_path).resolve()
        self.outbox_path = Path(outbox_path).resolve()
        self.process_policy = process_policy
        self.process_runner = process_runner
        self._gateway_token: object | None = None
        self._memory_lock = asyncio.Lock()
        self._outbox_lock = asyncio.Lock()

    def bind_gateway(self, token: object) -> None:
        if self._gateway_token is not None and self._gateway_token is not token:
            raise RuntimeError("CoreToolExecutor is already bound to another ToolGateway")
        self._gateway_token = token

    async def execute(
        self,
        envelope: ToolCallEnvelope,
        *,
        gateway_token: object | None = None,
    ) -> dict[str, Any]:
        if self._gateway_token is None or gateway_token is not self._gateway_token:
            raise AdapterBypassError("tool adapter may only be invoked by ToolGateway")
        try:
            tool = envelope.tool
            if tool in {"create_file", "write_file", "read_file", "delete_file", "list_dir"}:
                return await asyncio.to_thread(self._file_operation, envelope)
            if tool in {"memory_read", "memory_write"}:
                return await self._memory_operation(envelope)
            if tool == "send_email_dry_run":
                return await self._egress_operation(envelope)
            if tool == "run_shell":
                return await self._process_operation(envelope)
            raise ToolExecutionRejected(f"unsupported Core tool adapter: {tool}")
        except ToolExecutionRejected:
            raise
        except (FileExistsError, PathResolutionError, RestrictedShellPolicyError) as exc:
            raise ToolExecutionRejected(str(exc)) from exc

    def _file_operation(self, envelope: ToolCallEnvelope) -> dict[str, Any]:
        raw_path = envelope.canonical_args.get("path", envelope.resource)
        if not isinstance(raw_path, str) or raw_path != envelope.resource:
            raise ToolExecutionRejected(
                "file adapter path must exactly match ToolCallEnvelope.resource"
            )
        if envelope.tool == "read_file":
            payload = self.path_resolver.read_file_bytes(raw_path)
            # Match read_text's universal-newline behavior after the byte bound.
            try:
                content = payload.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
            except UnicodeDecodeError as exc:
                raise ToolExecutionRejected("read_file requires UTF-8 text content") from exc
            return {"tool": envelope.tool, "path": raw_path, "content": content}
        if envelope.tool == "list_dir":
            path = self.path_resolver.resolve_existing_dir(raw_path)
            # scandir is lazy; Path.iterdir can materialize the entire directory.
            with os.scandir(path) as entries:
                names = [entry.name for entry in islice(entries, self.max_list_entries + 1)]
            if len(names) > self.max_list_entries:
                raise ToolExecutionRejected(
                    f"directory exceeds configured entry limit: {self.max_list_entries}"
                )
            return {"tool": envelope.tool, "path": raw_path, "entries": sorted(names)}
        if envelope.tool == "delete_file":
            path = self.path_resolver.resolve_delete_target(raw_path)
            path.unlink()
            return {"tool": envelope.tool, "path": raw_path, "deleted": True}

        content = envelope.canonical_args.get("content")
        if not isinstance(content, str):
            raise ToolExecutionRejected(f"{envelope.tool} requires string content")
        self.path_resolver.validate_write_content(content)
        path = self.path_resolver.resolve_write_target(raw_path)
        if envelope.tool == "create_file":
            try:
                with path.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(content)
            except FileExistsError as exc:
                raise ToolExecutionRejected(
                    f"create_file target already exists: {raw_path}"
                ) from exc
        else:
            path.write_text(content, encoding="utf-8")
        return {
            "tool": envelope.tool,
            "path": raw_path,
            "bytes_written": len(content.encode("utf-8")),
        }

    async def _memory_operation(self, envelope: ToolCallEnvelope) -> dict[str, Any]:
        key = envelope.canonical_args.get("key", envelope.resource)
        if not isinstance(key, str) or key != envelope.resource or not key:
            raise ToolExecutionRejected("memory key must exactly match ToolCallEnvelope.resource")
        async with self._memory_lock:
            data = await asyncio.to_thread(self._load_memory)
            if envelope.tool == "memory_read":
                return {
                    "tool": envelope.tool,
                    "key": key,
                    "found": key in data,
                    "value": data.get(key),
                }
            value = envelope.canonical_args.get("value")
            data[key] = value
            await asyncio.to_thread(self._save_memory, data)
            return {"tool": envelope.tool, "key": key, "stored": True}

    def _load_memory(self) -> dict[str, Any]:
        if not self.memory_path.exists():
            return {}
        value = json.loads(self.memory_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Core memory store is invalid")
        return value

    def _save_memory(self, value: dict[str, Any]) -> None:
        self.memory_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fd, temporary = tempfile.mkstemp(prefix=".core-memory-", dir=self.memory_path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.memory_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    async def _egress_operation(self, envelope: ToolCallEnvelope) -> dict[str, Any]:
        recipient = envelope.canonical_args.get("to")
        subject = envelope.canonical_args.get("subject", "")
        body = envelope.canonical_args.get("body", "")
        if not all(isinstance(value, str) for value in (recipient, subject, body)) or not recipient:
            raise ToolExecutionRejected("send_email_dry_run requires to/subject/body strings")
        if envelope.resource not in {recipient, f"email:{recipient}"}:
            raise ToolExecutionRejected("egress recipient must match ToolCallEnvelope.resource")
        record = {
            "request_id": envelope.request_id,
            "task_id": envelope.task_id,
            "to": recipient,
            "subject": subject,
            "body": body,
            "created_at": datetime.now(UTC).isoformat(),
            "sent": False,
        }
        async with self._outbox_lock:
            await asyncio.to_thread(self._append_outbox, record)
        return {
            "tool": envelope.tool,
            "recipient": recipient,
            "simulated": True,
            "sent": False,
        }

    def _append_outbox(self, record: dict[str, Any]) -> None:
        self.outbox_path.parent.mkdir(parents=True, exist_ok=True)
        with self.outbox_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    async def _process_operation(self, envelope: ToolCallEnvelope) -> dict[str, Any]:
        if self.process_policy is None or self.process_runner is None:
            raise RuntimeError("restricted process adapter is not configured")
        cwd = envelope.canonical_args.get("cwd", ".")
        if not isinstance(cwd, str) or envelope.resource != cwd:
            raise ToolExecutionRejected(
                "run_shell cwd must exactly match ToolCallEnvelope.resource"
            )
        validated = self.process_policy.validate_request(envelope.canonical_args)
        record = await self.process_runner.run(validated)
        return {
            "tool": envelope.tool,
            "executable": record.executable,
            "arguments": list(record.arguments),
            "cwd": record.cwd,
            "exit_code": record.exit_code,
            "stdout": record.stdout,
            "stderr": record.stderr,
            "duration_ms": record.duration_ms,
            "environment_keys": list(record.environment_keys),
        }


def build_core_tool_executor(settings) -> CoreToolExecutor:
    """Build real adapters from trusted deployment Settings.

    The helper deliberately accepts Settings structurally to avoid a public dependency
    from the gateway contracts onto configuration internals.
    """

    import sys

    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    resolver = SafePathResolver(
        settings.workspace_root,
        max_path_length=settings.max_path_length,
        max_read_bytes=settings.max_read_bytes,
        max_write_bytes=settings.max_write_bytes,
    )
    process_policy: RestrictedShellPolicy | None = None
    process_runner: RestrictedProcessRunner | None = None
    policy_path = settings.security_config_dir / "tool_policies.yaml"
    try:
        executable_root = Path(sys.executable).resolve().parent.parent
        process_policy = RestrictedShellPolicy.from_yaml(
            policy_path,
            resolver,
            trusted_executable_roots=(executable_root,),
        )
        process_runner = RestrictedProcessRunner.from_yaml(policy_path)
    except (OSError, ValueError):
        # File/Memory/egress remain available; run_shell fails closed at use time.
        process_policy = None
        process_runner = None

    return CoreToolExecutor(
        path_resolver=resolver,
        memory_path=settings.core_memory_path,
        outbox_path=settings.core_outbox_path,
        process_policy=process_policy,
        process_runner=process_runner,
        max_list_entries=settings.max_list_entries,
    )
