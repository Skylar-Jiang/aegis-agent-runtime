from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ra_agent.contracts import (
    ExecutionStatus,
    MemoryStatus,
    SourceType,
    ToolCallRequest,
)
from ra_agent.memory import FilesystemMemoryStore, MemoryValueError
from ra_agent.tools.implementations.memory_tools import (
    MemoryReadHandler,
    MemoryToolError,
    MemoryWriteHandler,
)


def make_request(
    tool_name: str,
    *,
    request_id: str,
    arguments: dict[str, object],
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id=request_id,
        tool_name=tool_name,
        arguments=arguments,
        objective="use trusted runtime memory",
        context_summary="memory handler unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


@pytest.fixture
def store(tmp_path: Path) -> FilesystemMemoryStore:
    return FilesystemMemoryStore(tmp_path / "memory", max_value_bytes=4096)


@pytest.mark.asyncio
async def test_memory_write_stages_pending_memory_with_inspectable_artifacts(
    store: FilesystemMemoryStore,
) -> None:
    request = make_request(
        "memory_write",
        request_id="request-write-memory",
        arguments={"key": "user.preference", "value": {"theme": "dark"}},
    )

    result = await MemoryWriteHandler(store)(request)
    record = await store.get(request.request_id)

    assert result.status is ExecutionStatus.PENDING_COMMIT
    assert result.checkpoint_id is None
    assert record.status is MemoryStatus.PENDING
    assert result.output == {
        "staged": True,
        "memory_id": record.memory_id,
        "key": record.key,
        "status": "PENDING",
        "size_bytes": record.size_bytes,
    }
    assert [artifact["artifact_type"] for artifact in result.artifacts] == [
        "tool_output",
        "pending_memory",
    ]
    pending_artifact = result.artifacts[1]
    assert pending_artifact["memory_id"] == record.memory_id
    assert pending_artifact["key"] == record.key
    assert pending_artifact["path"] == record.payload_path
    assert pending_artifact["sha256"] == record.content_sha256
    assert pending_artifact["size_bytes"] == record.size_bytes
    assert pending_artifact["status"] == "PENDING"
    assert result.pending_changes == [
        {
            "operation": "MEMORY_WRITE",
            "memory_id": record.memory_id,
            "key": record.key,
            "content_sha256": record.content_sha256,
            "size_bytes": record.size_bytes,
            "status": "PENDING",
        }
    ]


@pytest.mark.asyncio
async def test_memory_read_never_exposes_pending_memory(
    store: FilesystemMemoryStore,
) -> None:
    await MemoryWriteHandler(store)(
        make_request(
            "memory_write",
            request_id="request-hidden-pending",
            arguments={"key": "secret.note", "value": "untrusted"},
        )
    )

    result = await MemoryReadHandler(store)(
        make_request(
            "memory_read",
            request_id="request-read-pending",
            arguments={"key": "secret.note"},
        )
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert result.output == {
        "found": False,
        "key": "secret.note",
        "value": None,
    }
    assert [artifact["artifact_type"] for artifact in result.artifacts] == [
        "tool_output"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [MemoryStatus.REJECTED, MemoryStatus.ROLLED_BACK])
async def test_memory_read_never_exposes_rejected_or_rolled_back_memory(
    store: FilesystemMemoryStore,
    terminal: MemoryStatus,
) -> None:
    write_request = make_request(
        "memory_write",
        request_id=f"request-hidden-{terminal.value.lower()}",
        arguments={"key": "unsafe.note", "value": "do not expose"},
    )
    await MemoryWriteHandler(store)(write_request)
    if terminal is MemoryStatus.REJECTED:
        await store.mark_rejected(write_request.request_id, reason="unsafe")
    else:
        await store.mark_rolled_back(write_request.request_id, reason="cancelled")

    result = await MemoryReadHandler(store)(
        make_request(
            "memory_read",
            request_id=f"request-read-{terminal.value.lower()}",
            arguments={"key": "unsafe.note"},
        )
    )

    assert result.output["found"] is False


@pytest.mark.asyncio
async def test_memory_read_returns_only_trusted_value(
    store: FilesystemMemoryStore,
) -> None:
    write_request = make_request(
        "memory_write",
        request_id="request-readable-trusted",
        arguments={"key": "project.setting", "value": {"enabled": True}},
    )
    await MemoryWriteHandler(store)(write_request)
    await store.mark_trusted(write_request.request_id)

    result = await MemoryReadHandler(store)(
        make_request(
            "memory_read",
            request_id="request-read-trusted",
            arguments={"key": "project.setting"},
        )
    )

    assert result.output == {
        "found": True,
        "memory_id": "memory-request-readable-trusted",
        "key": "project.setting",
        "value": {"enabled": True},
        "status": "TRUSTED",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"value": "missing key"},
        {"key": 123, "value": "invalid key"},
        {"key": "missing.value"},
    ],
)
async def test_memory_write_rejects_invalid_arguments(
    store: FilesystemMemoryStore,
    arguments: dict[str, object],
) -> None:
    with pytest.raises(MemoryToolError):
        await MemoryWriteHandler(store)(
            make_request(
                "memory_write",
                request_id="request-invalid-write-arguments",
                arguments=arguments,
            )
        )


@pytest.mark.asyncio
async def test_memory_read_rejects_invalid_key(store: FilesystemMemoryStore) -> None:
    with pytest.raises(MemoryToolError):
        await MemoryReadHandler(store)(
            make_request(
                "memory_read",
                request_id="request-invalid-read-key",
                arguments={"key": 123},
            )
        )


@pytest.mark.asyncio
async def test_memory_write_rejects_non_json_value(
    store: FilesystemMemoryStore,
) -> None:
    with pytest.raises(MemoryValueError):
        await MemoryWriteHandler(store)(
            make_request(
                "memory_write",
                request_id="request-invalid-json",
                arguments={"key": "invalid.value", "value": {1, 2, 3}},
            )
        )


class BlockingAfterStageStore(FilesystemMemoryStore):
    def __init__(self, memory_root: Path) -> None:
        super().__init__(memory_root, max_value_bytes=4096)
        self.staged = asyncio.Event()
        self.release = asyncio.Event()

    async def stage(
        self,
        request: ToolCallRequest,
        *,
        key: str,
        value: object,
    ):
        record = await super().stage(request, key=key, value=value)
        self.staged.set()
        await self.release.wait()
        return record


@pytest.mark.asyncio
async def test_memory_write_cancellation_rolls_back_pending_memory(
    tmp_path: Path,
) -> None:
    store = BlockingAfterStageStore(tmp_path / "memory")
    request = make_request(
        "memory_write",
        request_id="request-cancelled-write",
        arguments={"key": "cancelled.key", "value": "pending"},
    )
    task = asyncio.create_task(MemoryWriteHandler(store)(request))

    await store.staged.wait()
    task.cancel()
    store.release.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    record = await store.get(request.request_id)
    assert record.status is MemoryStatus.ROLLED_BACK
    assert record.rollback_reason == "memory_write cancelled"
    assert await store.get_trusted("cancelled.key") is None


@pytest.mark.asyncio
async def test_terminal_request_id_is_not_reexecuted_as_pending(
    store: FilesystemMemoryStore,
) -> None:
    request = make_request(
        "memory_write",
        request_id="request-terminal-retry",
        arguments={"key": "terminal.key", "value": "trusted"},
    )
    handler = MemoryWriteHandler(store)
    await handler(request)
    await store.mark_trusted(request.request_id)

    with pytest.raises(MemoryToolError, match="terminal state"):
        await handler(request)
