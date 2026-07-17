from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ra_agent.contracts import ExecutionStatus, SourceType, ToolCallRequest
from ra_agent.execution.checkpoint import (
    CheckpointConflictError,
    CheckpointIntegrityError,
    CheckpointNotFoundError,
    CheckpointStatus,
    FilesystemCheckpointManager,
    UnsupportedCheckpointToolError,
)
from ra_agent.tools.path_resolver import SafePathResolver, UnsafePathError


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "reports").mkdir()
    (root / "reports" / "result.md").write_text("old content", encoding="utf-8")
    (root / "old.txt").write_text("delete me", encoding="utf-8")
    return root


@pytest.fixture
def resolver(workspace: Path) -> SafePathResolver:
    return SafePathResolver(
        workspace,
        max_path_length=4096,
        max_read_bytes=1024 * 1024,
        max_write_bytes=1024 * 1024,
    )


@pytest.fixture
def checkpoint_root(tmp_path: Path) -> Path:
    return tmp_path / "checkpoints"


@pytest.fixture
def manager(
    checkpoint_root: Path,
    resolver: SafePathResolver,
) -> FilesystemCheckpointManager:
    return FilesystemCheckpointManager(checkpoint_root, resolver)


def make_request(
    tool_name: str,
    *,
    path: object,
    request_id: str,
    task_id: str = "task-1",
    step_id: str = "step-1",
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id=task_id,
        step_id=step_id,
        request_id=request_id,
        tool_name=tool_name,
        arguments={"path": path},
        objective="checkpoint a pending file change",
        context_summary="filesystem checkpoint manager unit test",
        source_type=SourceType.AGENT,
        requested_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_existing_write_target_is_backed_up_with_metadata(
    manager: FilesystemCheckpointManager,
    checkpoint_root: Path,
    workspace: Path,
) -> None:
    target = workspace / "reports" / "result.md"
    original_stat = target.stat()

    result = await manager.create(
        make_request(
            "write_file",
            path="reports/result.md",
            request_id="request-write-existing",
        )
    )

    assert result.status is ExecutionStatus.SUCCESS
    assert result.checkpoint_id == "checkpoint-request-write-existing"
    assert target.read_text(encoding="utf-8") == "old content"

    record = await manager.get(result.checkpoint_id)
    assert record.status is CheckpointStatus.CREATED
    assert record.tool_name == "write_file"
    assert record.target_paths == ("reports/result.md",)
    assert len(record.backups) == 1

    backup = record.backups[0]
    assert backup.existed is True
    assert backup.backup_path == "backups/0001.bin"
    assert backup.size_bytes == len(b"old content")
    assert backup.sha256 is not None
    assert backup.mtime_ns == original_stat.st_mtime_ns
    assert backup.mode is not None
    assert (
        checkpoint_root / result.checkpoint_id / "backups" / "0001.bin"
    ).read_bytes() == b"old content"


@pytest.mark.asyncio
async def test_new_write_target_records_existed_false_without_backup(
    manager: FilesystemCheckpointManager,
    checkpoint_root: Path,
    workspace: Path,
) -> None:
    target = workspace / "reports" / "new.md"

    result = await manager.create(
        make_request(
            "write_file",
            path="reports/new.md",
            request_id="request-write-new",
        )
    )

    assert not target.exists()

    record = await manager.get(result.checkpoint_id)
    backup = record.backups[0]
    assert backup.existed is False
    assert backup.backup_path is None
    assert backup.sha256 is None
    assert backup.size_bytes is None
    assert backup.mtime_ns is None
    assert backup.mode is None

    checkpoint_dir = checkpoint_root / result.checkpoint_id
    assert {path.name for path in checkpoint_dir.iterdir()} == {"manifest.json"}


@pytest.mark.asyncio
async def test_delete_target_is_backed_up_without_being_deleted(
    manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    target = workspace / "old.txt"

    result = await manager.create(
        make_request(
            "delete_file",
            path="old.txt",
            request_id="request-delete",
        )
    )

    assert target.is_file()
    assert target.read_text(encoding="utf-8") == "delete me"

    record = await manager.get(result.checkpoint_id)
    assert record.tool_name == "delete_file"
    assert record.backups[0].existed is True
    assert record.backups[0].backup_path == "backups/0001.bin"


@pytest.mark.asyncio
async def test_checkpoint_manifest_has_expected_fields_and_no_tmp_file(
    manager: FilesystemCheckpointManager,
    checkpoint_root: Path,
) -> None:
    result = await manager.create(
        make_request(
            "write_file",
            path="reports/result.md",
            request_id="request-manifest",
        )
    )

    checkpoint_dir = checkpoint_root / result.checkpoint_id
    manifest_path = checkpoint_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["version"] == 1
    assert manifest["checkpoint_id"] == result.checkpoint_id
    assert manifest["task_id"] == "task-1"
    assert manifest["step_id"] == "step-1"
    assert manifest["request_id"] == "request-manifest"
    assert manifest["tool_name"] == "write_file"
    assert manifest["target_paths"] == ["reports/result.md"]
    assert manifest["status"] == "CREATED"
    assert not (checkpoint_dir / "manifest.json.tmp").exists()


@pytest.mark.asyncio
async def test_same_checkpoint_request_is_idempotent(
    manager: FilesystemCheckpointManager,
    checkpoint_root: Path,
) -> None:
    request = make_request(
        "write_file",
        path="reports/result.md",
        request_id="request-idempotent",
    )

    first = await manager.create(request)
    first_manifest = (
        checkpoint_root / first.checkpoint_id / "manifest.json"
    ).read_bytes()
    second = await manager.create(request)
    second_manifest = (
        checkpoint_root / second.checkpoint_id / "manifest.json"
    ).read_bytes()

    assert first == second
    assert first_manifest == second_manifest
    assert await manager.verify_integrity(first.checkpoint_id) is True


@pytest.mark.asyncio
async def test_same_request_id_with_different_target_is_rejected(
    manager: FilesystemCheckpointManager,
) -> None:
    await manager.create(
        make_request(
            "write_file",
            path="reports/result.md",
            request_id="request-conflict",
        )
    )

    with pytest.raises(CheckpointConflictError):
        await manager.create(
            make_request(
                "write_file",
                path="reports/new.md",
                request_id="request-conflict",
            )
        )


@pytest.mark.asyncio
async def test_same_request_id_with_different_tool_is_rejected(
    manager: FilesystemCheckpointManager,
) -> None:
    await manager.create(
        make_request(
            "write_file",
            path="old.txt",
            request_id="request-tool-conflict",
        )
    )

    with pytest.raises(CheckpointConflictError):
        await manager.create(
            make_request(
                "delete_file",
                path="old.txt",
                request_id="request-tool-conflict",
            )
        )


@pytest.mark.asyncio
async def test_verify_integrity_detects_tampered_backup(
    manager: FilesystemCheckpointManager,
    checkpoint_root: Path,
) -> None:
    result = await manager.create(
        make_request(
            "delete_file",
            path="old.txt",
            request_id="request-tamper",
        )
    )

    backup_path = checkpoint_root / result.checkpoint_id / "backups" / "0001.bin"
    backup_path.write_bytes(b"tampered")

    assert await manager.verify_integrity(result.checkpoint_id) is False


@pytest.mark.asyncio
async def test_verify_integrity_detects_unexpected_file(
    manager: FilesystemCheckpointManager,
    checkpoint_root: Path,
) -> None:
    result = await manager.create(
        make_request(
            "write_file",
            path="reports/new.md",
            request_id="request-extra-file",
        )
    )

    (checkpoint_root / result.checkpoint_id / "unexpected.txt").write_text(
        "unexpected", encoding="utf-8"
    )

    assert await manager.verify_integrity(result.checkpoint_id) is False


@pytest.mark.asyncio
async def test_corrupted_manifest_is_rejected(
    manager: FilesystemCheckpointManager,
    checkpoint_root: Path,
) -> None:
    result = await manager.create(
        make_request(
            "write_file",
            path="reports/new.md",
            request_id="request-corrupt-manifest",
        )
    )
    manifest_path = checkpoint_root / result.checkpoint_id / "manifest.json"
    manifest_path.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(CheckpointIntegrityError):
        await manager.get(result.checkpoint_id)

    assert await manager.verify_integrity(result.checkpoint_id) is False


@pytest.mark.asyncio
async def test_unsupported_tool_is_rejected(
    manager: FilesystemCheckpointManager,
) -> None:
    with pytest.raises(UnsupportedCheckpointToolError):
        await manager.create(
            make_request(
                "read_file",
                path="old.txt",
                request_id="request-read",
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [None, "", "   "])
async def test_invalid_path_argument_is_rejected(
    manager: FilesystemCheckpointManager,
    path: object,
) -> None:
    with pytest.raises(ValueError):
        await manager.create(
            make_request(
                "write_file",
                path=path,
                request_id="request-invalid-path",
            )
        )


@pytest.mark.asyncio
async def test_path_traversal_is_rejected(
    manager: FilesystemCheckpointManager,
) -> None:
    with pytest.raises(UnsafePathError):
        await manager.create(
            make_request(
                "write_file",
                path="../outside.txt",
                request_id="request-traversal",
            )
        )


@pytest.mark.asyncio
async def test_unsafe_request_id_is_rejected(
    manager: FilesystemCheckpointManager,
) -> None:
    with pytest.raises(ValueError):
        await manager.create(
            make_request(
                "write_file",
                path="reports/new.md",
                request_id="../outside",
            )
        )


@pytest.mark.asyncio
async def test_get_missing_checkpoint_raises(
    manager: FilesystemCheckpointManager,
) -> None:
    with pytest.raises(CheckpointNotFoundError):
        await manager.get("checkpoint-missing")


@pytest.mark.asyncio
async def test_cleanup_is_idempotent(
    manager: FilesystemCheckpointManager,
) -> None:
    result = await manager.create(
        make_request(
            "write_file",
            path="reports/new.md",
            request_id="request-cleanup",
        )
    )

    await manager.cleanup(result.checkpoint_id)
    await manager.cleanup(result.checkpoint_id)

    assert not (manager.checkpoint_root / result.checkpoint_id).exists()


@pytest.mark.asyncio
async def test_manifest_failure_removes_partial_checkpoint(
    manager: FilesystemCheckpointManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_atomic_write = manager._atomic_write_bytes

    def fail_manifest(target: Path, content: bytes) -> None:
        if target.name == "manifest.json":
            raise OSError("simulated manifest failure")
        original_atomic_write(target, content)

    monkeypatch.setattr(manager, "_atomic_write_bytes", fail_manifest)

    with pytest.raises(OSError, match="simulated manifest failure"):
        await manager.create(
            make_request(
                "write_file",
                path="reports/result.md",
                request_id="request-failed-manifest",
            )
        )

    assert not (manager.checkpoint_root / "checkpoint-request-failed-manifest").exists()


@pytest.mark.asyncio
async def test_existing_file_permissions_are_recorded(
    manager: FilesystemCheckpointManager,
    workspace: Path,
) -> None:
    target = workspace / "reports" / "result.md"
    os.chmod(target, 0o600)

    result = await manager.create(
        make_request(
            "write_file",
            path="reports/result.md",
            request_id="request-mode",
        )
    )
    record = await manager.get(result.checkpoint_id)

    assert record.backups[0].mode is not None
