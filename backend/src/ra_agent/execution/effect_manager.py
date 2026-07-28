from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from ra_agent.contracts import (
    EffectRecord,
    EffectStatus,
    ExecutionStatus,
    ToolCallRequest,
    ToolExecutionResult,
)

from .artifacts import (
    PENDING_DELETE_ARTIFACT,
    PENDING_FILE_ARTIFACT,
    PENDING_MEMORY_ARTIFACT,
    QUARANTINED_DOWNLOAD_ARTIFACT,
    ArtifactContractError,
    validate_execution_artifacts,
)
from .effect_store import EffectNotFoundError, FilesystemEffectStore

_EFFECT_NAMESPACE = uuid.UUID("78aefca5-1f59-4b62-a452-f740df726393")
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")

FILE_WRITE_EFFECT = "FILE_WRITE"
FILE_DELETE_EFFECT = "FILE_DELETE"
MEMORY_WRITE_EFFECT = "MEMORY_WRITE"
DOWNLOAD_EFFECT = "DOWNLOAD"


class EffectManagerError(RuntimeError):
    """Base error raised while mapping durable resources to v0.4 effect facts."""


class EffectCorrelationError(EffectManagerError):
    """Raised when request, execution and effect facts disagree."""


class EffectPreconditionError(EffectManagerError):
    """Raised when an effect lifecycle action is invalid."""


class EffectArtifactError(EffectManagerError):
    """Raised when an execution cannot produce a safe artifact reference."""


class EffectManager:
    """Create and transition unified effect facts without deciding risk or approval."""

    def __init__(self, store: FilesystemEffectStore) -> None:
        self._store = store

    @property
    def store(self) -> FilesystemEffectStore:
        return self._store

    async def register_pending(
        self,
        request: ToolCallRequest,
        execution: ToolExecutionResult,
    ) -> EffectRecord:
        if execution.status is not ExecutionStatus.PENDING_COMMIT:
            raise EffectPreconditionError("effect registration requires PENDING_COMMIT execution")
        self._validate_correlation(request, execution)
        try:
            validate_execution_artifacts(request, execution)
        except ArtifactContractError as error:
            raise EffectArtifactError(f"execution artifacts are invalid: {error}") from error

        kind, target_ref, effect_artifact = self._describe_effect(request, execution)
        checkpoint_id = execution.checkpoint_id
        if request.tool_name in {"write_file", "delete_file"}:
            if checkpoint_id is None:
                raise EffectPreconditionError("filesystem effect requires checkpoint_id")
        elif checkpoint_id is not None:
            raise EffectCorrelationError("non-filesystem effect must not contain checkpoint_id")

        artifact_refs = [self._artifact_ref(effect_artifact)]
        record = EffectRecord(
            effect_id=self._effect_id(request.request_id, kind, target_ref),
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            kind=kind,
            target_ref=target_ref,
            status=EffectStatus.PENDING,
            checkpoint_id=checkpoint_id,
            artifact_refs=artifact_refs,
            created_at=datetime.now(UTC),
        )
        return await self._store.register(record)

    async def ensure_pending(
        self,
        request_id: str,
        *,
        checkpoint_id: str | None = None,
        target_ref: str | None = None,
        kind: str | None = None,
    ) -> EffectRecord:
        record = await self._require_by_request(request_id)
        if record.status is not EffectStatus.PENDING:
            raise EffectPreconditionError(f"effect must be PENDING, got {record.status.value}")
        self._validate_optional_fields(
            record,
            checkpoint_id=checkpoint_id,
            target_ref=target_ref,
            kind=kind,
        )
        return record

    async def mark_committed(
        self,
        request_id: str,
        *,
        checkpoint_id: str | None = None,
    ) -> EffectRecord:
        record = await self._require_by_request(request_id)
        self._validate_optional_fields(record, checkpoint_id=checkpoint_id)
        return await self._store.update_status(
            record.effect_id,
            expected_statuses=frozenset({EffectStatus.PENDING}),
            target_status=EffectStatus.COMMITTED,
        )

    async def mark_rejected(self, request_id: str) -> EffectRecord:
        record = await self._require_by_request(request_id)
        return await self._store.update_status(
            record.effect_id,
            expected_statuses=frozenset({EffectStatus.PENDING}),
            target_status=EffectStatus.REJECTED,
        )

    async def mark_rolled_back(
        self,
        request_id: str,
        *,
        missing_ok: bool = False,
    ) -> EffectRecord | None:
        record = await self._store.get_by_request_id(request_id)
        if record is None:
            if missing_ok:
                return None
            raise EffectNotFoundError(f"effect request not found: {request_id}")
        return await self._store.update_status(
            record.effect_id,
            expected_statuses=frozenset({EffectStatus.PENDING, EffectStatus.COMMITTED}),
            target_status=EffectStatus.ROLLED_BACK,
        )

    async def mark_cleaned(
        self,
        request_id: str,
        *,
        missing_ok: bool = False,
    ) -> EffectRecord | None:
        record = await self._store.get_by_request_id(request_id)
        if record is None:
            if missing_ok:
                return None
            raise EffectNotFoundError(f"effect request not found: {request_id}")
        return await self._store.update_status(
            record.effect_id,
            expected_statuses=frozenset({EffectStatus.PENDING}),
            target_status=EffectStatus.CLEANED,
        )

    async def _require_by_request(self, request_id: str) -> EffectRecord:
        record = await self._store.get_by_request_id(request_id)
        if record is None:
            raise EffectNotFoundError(f"effect request not found: {request_id}")
        return record

    @staticmethod
    def _validate_correlation(
        request: ToolCallRequest,
        execution: ToolExecutionResult,
    ) -> None:
        if execution.task_id != request.task_id:
            raise EffectCorrelationError("execution task_id does not match request")
        if execution.step_id != request.step_id:
            raise EffectCorrelationError("execution step_id does not match request")
        if execution.request_id != request.request_id:
            raise EffectCorrelationError("execution request_id does not match request")

    @staticmethod
    def _validate_optional_fields(
        record: EffectRecord,
        *,
        checkpoint_id: str | None = None,
        target_ref: str | None = None,
        kind: str | None = None,
    ) -> None:
        if checkpoint_id is not None and record.checkpoint_id != checkpoint_id:
            raise EffectCorrelationError("effect checkpoint_id does not match")
        if target_ref is not None and record.target_ref != target_ref:
            raise EffectCorrelationError("effect target_ref does not match")
        if kind is not None and record.kind != kind:
            raise EffectCorrelationError("effect kind does not match")

    def _describe_effect(
        self,
        request: ToolCallRequest,
        execution: ToolExecutionResult,
    ) -> tuple[str, str, Mapping[str, Any]]:
        mapping = {
            "write_file": (FILE_WRITE_EFFECT, PENDING_FILE_ARTIFACT),
            "delete_file": (FILE_DELETE_EFFECT, PENDING_DELETE_ARTIFACT),
            "memory_write": (MEMORY_WRITE_EFFECT, PENDING_MEMORY_ARTIFACT),
            "download_url": (DOWNLOAD_EFFECT, QUARANTINED_DOWNLOAD_ARTIFACT),
        }
        try:
            kind, artifact_type = mapping[request.tool_name]
        except KeyError as error:
            raise EffectPreconditionError(
                f"tool does not produce a managed effect: {request.tool_name}"
            ) from error
        artifacts = [
            item for item in execution.artifacts if item.get("artifact_type") == artifact_type
        ]
        if len(artifacts) != 1:
            raise EffectArtifactError(
                f"{request.tool_name} requires exactly one {artifact_type} artifact"
            )
        artifact = artifacts[0]
        if request.tool_name in {"write_file", "delete_file"}:
            target_ref = f"file:{self._safe_relative_path(artifact, 'target_path')}"
        elif request.tool_name == "memory_write":
            target_ref = f"memory:{self._safe_memory_key(artifact)}"
        else:
            target_ref = f"download:{request.request_id}"
        return kind, target_ref, artifact

    @staticmethod
    def _safe_relative_path(artifact: Mapping[str, Any], field_name: str) -> str:
        value = artifact.get(field_name)
        if not isinstance(value, str) or not value:
            raise EffectArtifactError(f"artifact {field_name} must be a non-empty string")
        if PureWindowsPath(value).is_absolute() or PurePosixPath(value).is_absolute():
            raise EffectArtifactError("effect file target must be workspace-relative")
        if "\\" in value:
            raise EffectArtifactError("effect file target must use POSIX separators")
        parts = PurePosixPath(value).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise EffectArtifactError("effect file target is unsafe")
        return PurePosixPath(*parts).as_posix()

    @staticmethod
    def _safe_memory_key(artifact: Mapping[str, Any]) -> str:
        key = artifact.get("key")
        if not isinstance(key, str) or not key.strip():
            raise EffectArtifactError("pending memory artifact must expose a non-empty key")
        if _CONTROL_PATTERN.search(key):
            raise EffectArtifactError("memory target key contains control characters")
        return key

    @staticmethod
    def _artifact_ref(artifact: Mapping[str, Any]) -> str:
        artifact_type = artifact.get("artifact_type")
        request_id = artifact.get("request_id")
        digest = artifact.get("sha256")
        size_bytes = artifact.get("size_bytes")
        if not isinstance(artifact_type, str) or not artifact_type:
            raise EffectArtifactError("artifact_type is missing")
        if not isinstance(request_id, str) or not request_id:
            raise EffectArtifactError("artifact request_id is missing")
        if not isinstance(digest, str) or len(digest) != 64:
            raise EffectArtifactError("artifact sha256 is invalid")
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 0:
            raise EffectArtifactError("artifact size_bytes is invalid")
        return f"artifact:{request_id}:{artifact_type}:{digest}:{size_bytes}"

    @staticmethod
    def _effect_id(request_id: str, kind: str, target_ref: str) -> str:
        identity = "\0".join((request_id, kind, target_ref))
        return str(uuid.uuid5(_EFFECT_NAMESPACE, identity))
