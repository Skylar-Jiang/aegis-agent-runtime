from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from ra_agent.contracts import (
    ExecutionStatus,
    SourceType,
    ToolCallRequest,
)
from ra_agent.tools.implementations.list_dir import ListDirHandler
from ra_agent.tools.implementations.read_file import ReadFileHandler
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

    (root / "docs").mkdir()
    (root / "README.md").write_text(
        "hello workspace",
        encoding="utf-8",
    )
    (root / "docs" / "report.txt").write_text(
        "test report",
        encoding="utf-8",
    )

    return root


@pytest.fixture
def resolver(workspace: Path) -> SafePathResolver:
    return SafePathResolver(
        workspace,
        max_path_length=4096,
        max_read_bytes=1024,
        max_write_bytes=1024,
    )


@pytest.fixture
def list_handler(
    resolver: SafePathResolver,
) -> ListDirHandler:
    return ListDirHandler(
        resolver,
        max_entries=1000,
    )


@pytest.fixture
def read_handler(
    resolver: SafePathResolver,
) -> ReadFileHandler:
    return ReadFileHandler(resolver)


def make_request(
    tool_name: str,
    *,
    arguments: dict[str, object],
    request_id: str = "request-1",
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id=request_id,
        tool_name=tool_name,
        arguments=arguments,
        objective="inspect workspace files",
        context_summary="unit test for safe file tools",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_list_dir_lists_safe_workspace_entries(
    list_handler: ListDirHandler,
) -> None:
    result = await list_handler(
        make_request(
            "list_dir",
            arguments={"path": "."},
        )
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert result.task_id == "task-1"
    assert result.step_id == "step-1"
    assert result.request_id == "request-1"
    assert result.output["path"] == "."
    assert result.output["returned_count"] == 2
    assert result.output["truncated"] is False

    entries = result.output["entries"]
    assert {entry["name"] for entry in entries} == {
        "README.md",
        "docs",
    }


@pytest.mark.asyncio
async def test_list_dir_lists_nested_directory(
    list_handler: ListDirHandler,
) -> None:
    result = await list_handler(
        make_request(
            "list_dir",
            arguments={"path": "docs"},
        )
    )

    assert result.output["path"] == "docs"
    assert result.output["entries"] == [
        {
            "name": "report.txt",
            "path": "docs/report.txt",
            "type": "file",
            "size_bytes": len(b"test report"),
        }
    ]


@pytest.mark.asyncio
async def test_list_dir_truncates_result(
    resolver: SafePathResolver,
    workspace: Path,
) -> None:
    for index in range(5):
        (workspace / f"file-{index}.txt").write_text(
            str(index),
            encoding="utf-8",
        )

    handler = ListDirHandler(
        resolver,
        max_entries=2,
    )

    result = await handler(
        make_request(
            "list_dir",
            arguments={"path": "."},
        )
    )

    assert result.output["returned_count"] == 2
    assert result.output["truncated"] is True


@pytest.mark.asyncio
async def test_list_dir_rejects_file_path(
    list_handler: ListDirHandler,
) -> None:
    with pytest.raises(PathTypeError):
        await list_handler(
            make_request(
                "list_dir",
                arguments={"path": "README.md"},
            )
        )


@pytest.mark.asyncio
async def test_list_dir_rejects_path_traversal(
    list_handler: ListDirHandler,
) -> None:
    with pytest.raises(UnsafePathError):
        await list_handler(
            make_request(
                "list_dir",
                arguments={"path": "../"},
            )
        )


@pytest.mark.asyncio
async def test_list_dir_requires_path_argument(
    list_handler: ListDirHandler,
) -> None:
    with pytest.raises(
        ValueError,
        match="path.*must be a string",
    ):
        await list_handler(
            make_request(
                "list_dir",
                arguments={},
            )
        )


@pytest.mark.asyncio
async def test_read_file_returns_content_and_hash(
    read_handler: ReadFileHandler,
) -> None:
    result = await read_handler(
        make_request(
            "read_file",
            arguments={"path": "README.md"},
        )
    )

    payload = b"hello workspace"

    assert result.status is ExecutionStatus.SUCCESS
    assert result.output == {
        "path": "README.md",
        "content": "hello workspace",
        "size_bytes": len(payload),
        "sha256": sha256(payload).hexdigest(),
        "encoding": "utf-8",
    }


@pytest.mark.asyncio
async def test_read_file_rejects_directory(
    read_handler: ReadFileHandler,
) -> None:
    with pytest.raises(PathTypeError):
        await read_handler(
            make_request(
                "read_file",
                arguments={"path": "docs"},
            )
        )


@pytest.mark.asyncio
async def test_read_file_rejects_missing_file(
    read_handler: ReadFileHandler,
) -> None:
    with pytest.raises(PathTypeError):
        await read_handler(
            make_request(
                "read_file",
                arguments={"path": "missing.txt"},
            )
        )


@pytest.mark.asyncio
async def test_read_file_rejects_sensitive_file(
    read_handler: ReadFileHandler,
    workspace: Path,
) -> None:
    (workspace / ".env").write_text(
        "API_KEY=test",
        encoding="utf-8",
    )

    with pytest.raises(SensitivePathError):
        await read_handler(
            make_request(
                "read_file",
                arguments={"path": ".env"},
            )
        )


@pytest.mark.asyncio
async def test_read_file_rejects_large_file(
    workspace: Path,
) -> None:
    (workspace / "large.txt").write_bytes(b"x" * 11)

    resolver = SafePathResolver(
        workspace,
        max_path_length=4096,
        max_read_bytes=10,
        max_write_bytes=1024,
    )
    handler = ReadFileHandler(resolver)

    with pytest.raises(PathSizeError):
        await handler(
            make_request(
                "read_file",
                arguments={"path": "large.txt"},
            )
        )


@pytest.mark.asyncio
async def test_read_file_rejects_non_utf8_content(
    read_handler: ReadFileHandler,
    workspace: Path,
) -> None:
    (workspace / "binary.bin").write_bytes(b"\xff\xfe\x00\x01")

    with pytest.raises(
        ValueError,
        match="not valid UTF-8",
    ):
        await read_handler(
            make_request(
                "read_file",
                arguments={"path": "binary.bin"},
            )
        )


@pytest.mark.asyncio
async def test_handler_rejects_wrong_tool_name(
    read_handler: ReadFileHandler,
) -> None:
    with pytest.raises(
        ValueError,
        match="cannot execute tool",
    ):
        await read_handler(
            make_request(
                "list_dir",
                arguments={"path": "README.md"},
            )
        )


@pytest.mark.asyncio
async def test_list_dir_does_not_follow_symbolic_links(
    list_handler: ListDirHandler,
    workspace: Path,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()

    (outside / "secret.txt").write_text(
        "outside secret",
        encoding="utf-8",
    )

    link = workspace / "outside-link"

    try:
        link.symlink_to(
            outside,
            target_is_directory=True,
        )
    except (NotImplementedError, OSError) as error:
        pytest.skip(f"symbolic links are unavailable: {error}")

    result = await list_handler(
        make_request(
            "list_dir",
            arguments={"path": "."},
        )
    )

    names = {entry["name"] for entry in result.output["entries"]}

    assert "outside-link" not in names
    assert "secret.txt" not in names


@pytest.mark.asyncio
async def test_read_file_preserves_correlation_ids(
    read_handler: ReadFileHandler,
) -> None:
    request = make_request(
        "read_file",
        arguments={"path": "README.md"},
        request_id="request-special",
    )

    result = await read_handler(request)

    assert result.task_id == request.task_id
    assert result.step_id == request.step_id
    assert result.request_id == request.request_id


def snapshot_workspace(
    workspace: Path,
) -> dict[str, bytes]:
    return {
        path.relative_to(workspace).as_posix(): path.read_bytes()
        for path in workspace.rglob("*")
        if path.is_file()
    }


@pytest.mark.asyncio
async def test_read_handlers_do_not_modify_workspace(
    list_handler: ListDirHandler,
    read_handler: ReadFileHandler,
    workspace: Path,
) -> None:
    before = snapshot_workspace(workspace)

    await list_handler(
        make_request(
            "list_dir",
            arguments={"path": "."},
        )
    )

    await read_handler(
        make_request(
            "read_file",
            arguments={"path": "README.md"},
        )
    )

    after = snapshot_workspace(workspace)

    assert after == before


def test_list_dir_requires_positive_max_entries(
    resolver: SafePathResolver,
) -> None:
    with pytest.raises(
        ValueError,
        match="max_entries must be positive",
    ):
        ListDirHandler(
            resolver,
            max_entries=0,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"path": None},
        {"path": ""},
        {"path": "   "},
    ],
)
async def test_read_file_rejects_invalid_path_argument(
    read_handler: ReadFileHandler,
    arguments: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        await read_handler(
            make_request(
                "read_file",
                arguments=arguments,
            )
        )


@pytest.mark.asyncio
async def test_list_dir_hides_sensitive_entries(
    list_handler: ListDirHandler,
    workspace: Path,
) -> None:
    (workspace / ".env").write_text(
        "SECRET=value",
        encoding="utf-8",
    )
    (workspace / "private.key").write_text(
        "private",
        encoding="utf-8",
    )

    result = await list_handler(
        make_request(
            "list_dir",
            arguments={"path": "."},
        )
    )

    names = {entry["name"] for entry in result.output["entries"]}

    assert ".env" not in names
    assert "private.key" not in names
