from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ra_agent.contracts import (
    ExecutionStatus,
    MemoryStatus,
    PostCheckResult,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.execution.artifacts import (
    PENDING_MEMORY_ARTIFACT,
    ArtifactContractError,
    validate_execution_artifacts,
)

from .models import MemoryRecord
from .store import FilesystemMemoryStore, MemoryIntegrityError


class MemoryLifecycleError(RuntimeError):
    """Base error raised by memory commit, rejection and rollback coordination."""


class MemoryLifecycleCorrelationError(MemoryLifecycleError):
    """Raised when request, execution, artifact or persisted memory IDs disagree."""


class MemoryLifecyclePreconditionError(MemoryLifecycleError):
    """Raised when a lifecycle action is incompatible with PostCheck or status."""


class MemoryLifecycleIntegrityError(MemoryLifecycleError):
    """Raised when pending memory or its execution artifact fails verification."""


class MemoryLifecycleManager:
    """Apply PostCheck decisions to durable pending memory without implementing checks."""

    def __init__(self, store: FilesystemMemoryStore) -> None:
        self._store = store

    @property
    def store(self) -> FilesystemMemoryStore:
        return self._store

    async def commit(
        self,
        request: ToolCallRequest,
        execution: ToolExecutionResult,
        post_check: PostCheckResult,
    ) -> MemoryRecord:
        """Trust pending memory only after a passing, correlated PostCheck result."""

        if not post_check.passed:
            raise MemoryLifecyclePreconditionError(
                "memory commit requires a passing PostCheck result"
            )

        record = await self._validate_pending_action(request, execution, post_check)
        if record.status is not MemoryStatus.PENDING:
            if record.status is MemoryStatus.TRUSTED:
                return record
            raise MemoryLifecyclePreconditionError(
                f"memory commit requires PENDING state, got {record.status.value}"
            )
        return await self._store.mark_trusted(request.request_id)

    async def reject(
        self,
        request: ToolCallRequest,
        execution: ToolExecutionResult,
        post_check: PostCheckResult,
    ) -> MemoryRecord:
        """Reject pending memory after a failing, correlated PostCheck result."""

        if post_check.passed:
            raise MemoryLifecyclePreconditionError(
                "memory rejection requires a failing PostCheck result"
            )

        record = await self._validate_pending_action(request, execution, post_check)
        if record.status is not MemoryStatus.PENDING:
            if record.status is MemoryStatus.REJECTED:
                return record
            raise MemoryLifecyclePreconditionError(
                f"memory rejection requires PENDING state, got {record.status.value}"
            )
        return await self._store.mark_rejected(
            request.request_id,
            reason=post_check.reason,
        )

    async def rollback(self, request_id: str, *, reason: str) -> MemoryRecord:
        """Rollback a pending or just-trusted memory version idempotently."""

        if not await self._store.verify_integrity(request_id):
            raise MemoryLifecycleIntegrityError(
                "memory record failed integrity verification before rollback"
            )
        return await self._store.mark_rolled_back(request_id, reason=reason)

    async def _validate_pending_action(
        self,
        request: ToolCallRequest,
        execution: ToolExecutionResult,
        post_check: PostCheckResult,
    ) -> MemoryRecord:
        if request.tool_name != "memory_write":
            raise MemoryLifecyclePreconditionError("memory lifecycle actions require memory_write")

        if execution.status is not ExecutionStatus.PENDING_COMMIT:
            raise MemoryLifecyclePreconditionError(
                "memory lifecycle actions require PENDING_COMMIT execution"
            )

        if not (request.request_id == execution.request_id == post_check.request_id):
            raise MemoryLifecycleCorrelationError(
                "request_id does not match across memory lifecycle records"
            )

        if execution.task_id != request.task_id or execution.step_id != request.step_id:
            raise MemoryLifecycleCorrelationError(
                "execution task_id or step_id does not match memory request"
            )

        try:
            validate_execution_artifacts(request, execution)
        except ArtifactContractError as error:
            raise MemoryLifecycleIntegrityError(
                f"memory execution artifacts are invalid: {error}"
            ) from error

        pending_artifacts = [
            artifact
            for artifact in execution.artifacts
            if artifact.get("artifact_type") == PENDING_MEMORY_ARTIFACT
        ]
        if len(pending_artifacts) != 1:
            raise MemoryLifecycleIntegrityError(
                "memory execution requires exactly one pending_memory artifact"
            )
        artifact = pending_artifacts[0]

        if not await self._store.verify_integrity(request.request_id):
            raise MemoryLifecycleIntegrityError("pending memory failed integrity verification")

        try:
            record = await self._store.get(request.request_id)
        except MemoryIntegrityError as error:
            raise MemoryLifecycleIntegrityError("pending memory record cannot be loaded") from error

        self._validate_record_correlation(request, record)
        self._validate_artifact_against_record(artifact, record)
        self._validate_pending_change(execution.pending_changes, record)
        return record

    @staticmethod
    def _validate_record_correlation(
        request: ToolCallRequest,
        record: MemoryRecord,
    ) -> None:
        if record.task_id != request.task_id:
            raise MemoryLifecycleCorrelationError("memory record task_id does not match request")
        if record.step_id != request.step_id:
            raise MemoryLifecycleCorrelationError("memory record step_id does not match request")
        if record.request_id != request.request_id:
            raise MemoryLifecycleCorrelationError("memory record request_id does not match request")
        if record.tool_name != request.tool_name:
            raise MemoryLifecycleCorrelationError("memory record tool_name does not match request")

    @staticmethod
    def _validate_artifact_against_record(
        artifact: Mapping[str, Any],
        record: MemoryRecord,
    ) -> None:
        expected: dict[str, object] = {
            "memory_id": record.memory_id,
            "key": record.key,
            "path": record.payload_path,
            "status": MemoryStatus.PENDING.value,
            "sha256": record.content_sha256,
            "size_bytes": record.size_bytes,
        }
        for field_name, expected_value in expected.items():
            if artifact.get(field_name) != expected_value:
                raise MemoryLifecycleIntegrityError(
                    f"pending_memory artifact {field_name} does not match persisted memory"
                )

    @staticmethod
    def _validate_pending_change(
        pending_changes: list[dict[str, Any]],
        record: MemoryRecord,
    ) -> None:
        if len(pending_changes) != 1:
            raise MemoryLifecycleIntegrityError(
                "memory execution requires exactly one pending change"
            )

        change = pending_changes[0]
        expected: dict[str, object] = {
            "operation": "MEMORY_WRITE",
            "memory_id": record.memory_id,
            "key": record.key,
            "content_sha256": record.content_sha256,
            "size_bytes": record.size_bytes,
            "status": MemoryStatus.PENDING.value,
        }
        for field_name, expected_value in expected.items():
            if change.get(field_name) != expected_value:
                raise MemoryLifecycleIntegrityError(
                    f"pending memory change {field_name} does not match persisted memory"
                )
