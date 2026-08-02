from datetime import UTC, datetime
from pathlib import Path

import pytest
from ra_agent.contracts import (
    ExecutionStatus,
    SourceType,
    ToolCallRequest,
)
from ra_agent.execution.pending_store import (
    PendingConflictError,
    PendingOperation,
    PendingStatus,
    PendingStore,
)
from ra_agent.tools.implementations.delete_file import (
    DeleteFileHandler,
)
from ra_agent.tools.implementations.write_file import (
    WriteFileHandler,
)
from ra_agent.tools.path_resolver import (
    PathSizeError,
    PathTypeError,
    SafePathResolver,
    SensitivePathError,
    UnsafePathError,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()

    (root / "reports").mkdir()
    (root / "reports" / "result.md").write_text(
        "old content",
        encoding="utf-8",
    )
    (root / "old.txt").write_text(
        "keep until commit",
        encoding="utf-8",
    )
    (root / "directory").mkdir()

    return root


@pytest.fixture
def pending_root(tmp_path: Path) -> Path:
    root = tmp_path / "pending"
    root.mkdir()
    return root


@pytest.fixture
def resolver(
    workspace: Path,
) -> SafePathResolver:
    return SafePathResolver(
        workspace,
        max_path_length=4096,
        max_read_bytes=1024,
        max_write_bytes=1024,
    )


@pytest.fixture
def pending_store(
    pending_root: Path,
) -> PendingStore:
    return PendingStore(pending_root)


@pytest.fixture
def write_handler(
    resolver: SafePathResolver,
    pending_store: PendingStore,
) -> WriteFileHandler:
    return WriteFileHandler(
        resolver,
        pending_store,
    )


@pytest.fixture
def delete_handler(
    resolver: SafePathResolver,
    pending_store: PendingStore,
) -> DeleteFileHandler:
    return DeleteFileHandler(
        resolver,
        pending_store,
    )


def make_request(
    tool_name: str,
    *,
    arguments: dict[str, object],
    request_id: str,
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id=request_id,
        tool_name=tool_name,
        arguments=arguments,
        objective="modify workspace files safely",
        context_summary="unit test for pending file tools",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_write_file_stages_content_without_overwriting_workspace(
    write_handler: WriteFileHandler,
    pending_store: PendingStore,
    workspace: Path,
    pending_root: Path,
) -> None:
    target = workspace / "reports" / "result.md"

    result = await write_handler(
        make_request(
            "write_file",
            arguments={
                "path": "reports/result.md",
                "content": "new content",
            },
            request_id="request-write-1",
        )
    )

    assert result.status is ExecutionStatus.PENDING_COMMIT
    assert result.task_id == "task-1"
    assert result.step_id == "step-1"
    assert result.request_id == "request-write-1"
    assert result.checkpoint_id is None

    assert target.read_text(encoding="utf-8") == "old content"

    record = await pending_store.get("request-write-1")

    assert record.operation is PendingOperation.WRITE
    assert record.status is PendingStatus.PENDING
    assert record.checkpoint_id is None
    assert record.target_path == "reports/result.md"

    assert record.pending_path is not None
    assert (pending_root / record.pending_path).read_bytes() == b"new content"

    assert result.pending_changes[0]["operation"] == "WRITE"
    assert result.pending_changes[0]["target_path"] == "reports/result.md"

    assert [artifact["artifact_type"] for artifact in result.artifacts] == [
        "tool_output",
        "pending_file",
    ]
    output_artifact, pending_artifact = result.artifacts
    assert output_artifact["request_id"] == result.request_id
    assert output_artifact["tool_name"] == "write_file"
    assert output_artifact["status"] == "PENDING_COMMIT"
    assert pending_artifact["request_id"] == result.request_id
    assert pending_artifact["tool_name"] == "write_file"
    assert pending_artifact["status"] == "PENDING"
    assert pending_artifact["path"] == record.pending_path
    assert pending_artifact["target_path"] == record.target_path
    assert pending_artifact["sha256"] == record.content_sha256
    assert pending_artifact["size_bytes"] == record.size_bytes


@pytest.mark.asyncio
async def test_write_file_new_target_is_not_created_before_commit(
    write_handler: WriteFileHandler,
    workspace: Path,
) -> None:
    target = workspace / "reports" / "new.md"

    result = await write_handler(
        make_request(
            "write_file",
            arguments={
                "path": "reports/new.md",
                "content": "pending",
            },
            request_id="request-write-2",
        )
    )

    assert result.status is ExecutionStatus.PENDING_COMMIT
    assert not target.exists()


@pytest.mark.asyncio
async def test_write_file_is_idempotent_for_same_request(
    write_handler: WriteFileHandler,
    pending_store: PendingStore,
) -> None:
    request = make_request(
        "write_file",
        arguments={
            "path": "reports/result.md",
            "content": "same",
        },
        request_id="request-write-3",
    )

    first = await write_handler(request)
    second = await write_handler(request)

    assert first.pending_changes == second.pending_changes

    record = await pending_store.get("request-write-3")
    assert record.target_path == "reports/result.md"


@pytest.mark.asyncio
async def test_write_file_rejects_changed_semantics_for_same_request(
    write_handler: WriteFileHandler,
) -> None:
    await write_handler(
        make_request(
            "write_file",
            arguments={
                "path": "reports/result.md",
                "content": "first",
            },
            request_id="request-write-4",
        )
    )

    with pytest.raises(PendingConflictError):
        await write_handler(
            make_request(
                "write_file",
                arguments={
                    "path": "reports/result.md",
                    "content": "second",
                },
                request_id="request-write-4",
            )
        )


@pytest.mark.asyncio
async def test_write_file_rejects_invalid_content(
    write_handler: WriteFileHandler,
) -> None:
    with pytest.raises(
        ValueError,
        match="content.*must be a string",
    ):
        await write_handler(
            make_request(
                "write_file",
                arguments={
                    "path": "reports/result.md",
                    "content": 123,
                },
                request_id="request-write-5",
            )
        )


@pytest.mark.asyncio
async def test_write_file_rejects_large_content(
    workspace: Path,
    pending_store: PendingStore,
) -> None:
    resolver = SafePathResolver(
        workspace,
        max_path_length=4096,
        max_read_bytes=1024,
        max_write_bytes=4,
    )
    handler = WriteFileHandler(
        resolver,
        pending_store,
    )

    with pytest.raises(PathSizeError):
        await handler(
            make_request(
                "write_file",
                arguments={
                    "path": "reports/result.md",
                    "content": "12345",
                },
                request_id="request-write-6",
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "expected_error"),
    [
        ("../outside.txt", UnsafePathError),
        (".env", SensitivePathError),
        ("missing/result.txt", PathTypeError),
        ("directory", PathTypeError),
    ],
)
async def test_write_file_rejects_unsafe_target(
    write_handler: WriteFileHandler,
    path: str,
    expected_error: type[Exception],
) -> None:
    with pytest.raises(expected_error):
        await write_handler(
            make_request(
                "write_file",
                arguments={
                    "path": path,
                    "content": "content",
                },
                request_id=f"request-unsafe-{path.replace('/', '-')}",
            )
        )


@pytest.mark.asyncio
async def test_delete_file_stages_marker_without_deleting_file(
    delete_handler: DeleteFileHandler,
    pending_store: PendingStore,
    workspace: Path,
    pending_root: Path,
) -> None:
    target = workspace / "old.txt"

    result = await delete_handler(
        make_request(
            "delete_file",
            arguments={"path": "old.txt"},
            request_id="request-delete-1",
        )
    )

    assert result.status is ExecutionStatus.PENDING_COMMIT
    assert result.task_id == "task-1"
    assert result.step_id == "step-1"
    assert result.request_id == "request-delete-1"
    assert result.checkpoint_id is None

    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "keep until commit"

    record = await pending_store.get("request-delete-1")

    assert record.operation is PendingOperation.DELETE
    assert record.target_path == "old.txt"
    assert record.pending_path is None

    request_dir = pending_root / "request-delete-1"
    assert (request_dir / "manifest.json").is_file()
    assert not (request_dir / "payload.bin").exists()

    assert result.pending_changes == [
        {
            "operation": "DELETE",
            "target_path": "old.txt",
            "original_size_bytes": len(b"keep until commit"),
            "status": "PENDING",
        }
    ]

    assert [artifact["artifact_type"] for artifact in result.artifacts] == [
        "tool_output",
        "pending_delete",
    ]
    output_artifact, delete_artifact = result.artifacts
    assert output_artifact["request_id"] == result.request_id
    assert output_artifact["tool_name"] == "delete_file"
    assert output_artifact["status"] == "PENDING_COMMIT"
    assert delete_artifact["request_id"] == result.request_id
    assert delete_artifact["tool_name"] == "delete_file"
    assert delete_artifact["status"] == "PENDING"
    assert delete_artifact["target_path"] == "old.txt"
    assert len(delete_artifact["sha256"]) == 64
    assert delete_artifact["size_bytes"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "expected_error"),
    [
        ("missing.txt", PathTypeError),
        ("directory", PathTypeError),
        ("../outside.txt", UnsafePathError),
        (".env", SensitivePathError),
    ],
)
async def test_delete_file_rejects_invalid_target(
    delete_handler: DeleteFileHandler,
    path: str,
    expected_error: type[Exception],
) -> None:
    with pytest.raises(expected_error):
        await delete_handler(
            make_request(
                "delete_file",
                arguments={"path": path},
                request_id=f"request-delete-{path.replace('/', '-')}",
            )
        )


@pytest.mark.asyncio
async def test_write_handler_rejects_wrong_tool_name(
    write_handler: WriteFileHandler,
) -> None:
    with pytest.raises(
        ValueError,
        match="cannot execute tool",
    ):
        await write_handler(
            make_request(
                "read_file",
                arguments={
                    "path": "reports/result.md",
                    "content": "content",
                },
                request_id="request-wrong-write",
            )
        )


@pytest.mark.asyncio
async def test_delete_handler_rejects_wrong_tool_name(
    delete_handler: DeleteFileHandler,
) -> None:
    with pytest.raises(
        ValueError,
        match="cannot execute tool",
    ):
        await delete_handler(
            make_request(
                "write_file",
                arguments={"path": "old.txt"},
                request_id="request-wrong-delete",
            )
        )
