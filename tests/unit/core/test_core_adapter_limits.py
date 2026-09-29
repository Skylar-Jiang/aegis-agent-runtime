"""Core filesystem adapters enforce limits at the actual I/O boundary."""

from contextlib import contextmanager
from pathlib import Path

import pytest
from ra_agent.contracts.core_v1 import (
    ContractVersionRef,
    ContractVersionStatus,
    EffectClass,
    ToolCallEnvelope,
)
from ra_agent.core.config import Settings
from ra_agent.gateway import CoreToolExecutor, ToolExecutionRejected, build_core_tool_executor
from ra_agent.tools.path_resolver import SafePathResolver


def _read_call(tool: str, resource: str) -> ToolCallEnvelope:
    return ToolCallEnvelope(
        request_id="bounded-read",
        task_id="read-task",
        session_id="read-session",
        contract_ref=ContractVersionRef(
            contract_id="read-contract",
            version=1,
            digest="read-contract-digest",
            status=ContractVersionStatus.CONFIRMED,
        ),
        skill_ref="reader",
        tool=tool,
        action=tool,
        effect_class=EffectClass.READ,
        resource=resource,
        canonical_args={"path": resource},
    )


@pytest.mark.asyncio
async def test_core_directory_listing_respects_the_configured_limit(tmp_path):
    workspace = tmp_path / "workspace"
    reports = workspace / "reports"
    reports.mkdir(parents=True)
    (reports / "b.txt").write_text("b")
    (reports / "a.txt").write_text("a")
    executor = build_core_tool_executor(
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            workspace_root=workspace,
            max_list_entries=2,
            core_memory_path=tmp_path / "memory.json",
            core_outbox_path=tmp_path / "outbox.jsonl",
        )
    )
    gateway_token = object()
    executor.bind_gateway(gateway_token)
    call = _read_call("list_dir", "reports")
    result = await executor.execute(call, gateway_token=gateway_token)
    assert result["entries"] == ["a.txt", "b.txt"]

    (reports / "c.txt").write_text("c")
    with pytest.raises(ToolExecutionRejected, match="directory.*limit|entry limit"):
        await executor.execute(call, gateway_token=gateway_token)


@pytest.mark.asyncio
async def test_core_file_read_is_bounded_when_file_grows_after_resolution(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "report.txt"
    target.write_bytes(b"small")
    read_sizes = []
    original_open = Path.open

    class RecordingReader:
        def __init__(self, stream):
            self.stream = stream

        def read(self, size=-1):
            read_sizes.append(size)
            return self.stream.read(size)

    @contextmanager
    def recording_open(path, mode="r", *args, **kwargs):
        with original_open(path, mode, *args, **kwargs) as stream:
            yield RecordingReader(stream) if path == target and "r" in mode else stream

    class GrowingFileResolver(SafePathResolver):
        def resolve_existing_file(self, raw_path):
            path = super().resolve_existing_file(raw_path)
            # The real stat check sees five bytes; the following actual read sees
            # growth. This deterministically reproduces the filesystem race.
            path.write_bytes(b"x" * 1024)
            return path

    resolver = GrowingFileResolver(
        workspace, max_path_length=4096, max_read_bytes=8, max_write_bytes=1024
    )
    executor = CoreToolExecutor(
        path_resolver=resolver,
        memory_path=tmp_path / "memory.json",
        outbox_path=tmp_path / "outbox.jsonl",
    )
    gateway_token = object()
    executor.bind_gateway(gateway_token)
    monkeypatch.setattr(Path, "open", recording_open)
    with pytest.raises(ToolExecutionRejected, match="read limit"):
        await executor.execute(_read_call("read_file", "report.txt"), gateway_token=gateway_token)
    # Verify the real stream was bounded, rather than fully read then rejected.
    assert read_sizes == [9]
    assert target.stat().st_size == 1024
