from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ra_agent.contracts import (
    ExecutionStatus,
    PostCheckResult,
    ToolCallRequest,
    ToolExecutionResult,
)

from .artifacts import (
    QUARANTINED_DOWNLOAD_ARTIFACT,
    ArtifactContractError,
    validate_execution_artifacts,
)
from .effect_manager import EffectManager
from .quarantine import (
    FilesystemQuarantineStore,
    QuarantineIntegrityError,
    QuarantineRecord,
    QuarantineStatus,
)


class DownloadLifecycleError(RuntimeError):
    """Base error raised by download commit, rejection and rollback coordination."""


class DownloadLifecycleCorrelationError(DownloadLifecycleError):
    """Raised when request, execution, artifact or persisted IDs disagree."""


class DownloadLifecyclePreconditionError(DownloadLifecycleError):
    """Raised when a lifecycle action is incompatible with status or PostCheck."""


class DownloadLifecycleIntegrityError(DownloadLifecycleError):
    """Raised when quarantine metadata, payload or artifacts fail verification."""


class DownloadLifecycleManager:
    """Apply PostCheck decisions to quarantine without implementing security checks."""

    def __init__(
        self,
        store: FilesystemQuarantineStore,
        effect_manager: EffectManager | None = None,
    ) -> None:
        self._store = store
        self._effect_manager = effect_manager

    @property
    def store(self) -> FilesystemQuarantineStore:
        return self._store

    async def commit(
        self,
        request: ToolCallRequest,
        execution: ToolExecutionResult,
        post_check: PostCheckResult,
    ) -> QuarantineRecord:
        """Approve a quarantined artifact after a passing PostCheck.

        download_url has no workspace destination in the frozen ToolSpec, so COMMITTED
        means approved quarantine content. Moving it into workspace requires a future
        explicit file-write request and is intentionally outside this manager.
        """

        if not post_check.passed:
            raise DownloadLifecyclePreconditionError(
                "download commit requires a passing PostCheck result"
            )
        record = await self._validate_pending_action(request, execution, post_check)
        if record.status is QuarantineStatus.COMMITTED:
            if self._effect_manager is not None:
                await self._effect_manager.mark_committed(request.request_id)
            return record
        if record.status is not QuarantineStatus.QUARANTINED:
            raise DownloadLifecyclePreconditionError(
                f"download commit requires QUARANTINED state, got {record.status.value}"
            )
        committed = await self._store.mark_committed(request.request_id)
        if self._effect_manager is not None:
            try:
                await self._effect_manager.mark_committed(request.request_id)
            except Exception:
                await self._store.mark_rolled_back(
                    request.request_id,
                    reason="effect commit update failed",
                )
                raise
        return committed

    async def reject(
        self,
        request: ToolCallRequest,
        execution: ToolExecutionResult,
        post_check: PostCheckResult,
    ) -> QuarantineRecord:
        if post_check.passed:
            raise DownloadLifecyclePreconditionError(
                "download rejection requires a failing PostCheck result"
            )
        record = await self._validate_pending_action(request, execution, post_check)
        if record.status is QuarantineStatus.REJECTED:
            if self._effect_manager is not None:
                await self._effect_manager.mark_rejected(request.request_id)
            return record
        if record.status is not QuarantineStatus.QUARANTINED:
            raise DownloadLifecyclePreconditionError(
                f"download rejection requires QUARANTINED state, got {record.status.value}"
            )
        rejected = await self._store.mark_rejected(
            request.request_id,
            reason=post_check.reason,
        )
        if self._effect_manager is not None:
            await self._effect_manager.mark_rejected(request.request_id)
        return rejected

    async def rollback(self, request_id: str, *, reason: str) -> QuarantineRecord:
        if not await self._store.verify_integrity(request_id):
            raise DownloadLifecycleIntegrityError(
                "quarantine failed integrity verification before rollback"
            )
        rolled_back = await self._store.mark_rolled_back(request_id, reason=reason)
        if self._effect_manager is not None:
            await self._effect_manager.mark_rolled_back(request_id, missing_ok=True)
        return rolled_back

    async def _validate_pending_action(
        self,
        request: ToolCallRequest,
        execution: ToolExecutionResult,
        post_check: PostCheckResult,
    ) -> QuarantineRecord:
        if request.tool_name != "download_url":
            raise DownloadLifecyclePreconditionError(
                "download lifecycle actions require download_url"
            )
        if execution.status is not ExecutionStatus.PENDING_COMMIT:
            raise DownloadLifecyclePreconditionError(
                "download lifecycle actions require PENDING_COMMIT execution"
            )
        if not (request.request_id == execution.request_id == post_check.request_id):
            raise DownloadLifecycleCorrelationError(
                "request_id does not match across download lifecycle records"
            )
        if execution.task_id != request.task_id or execution.step_id != request.step_id:
            raise DownloadLifecycleCorrelationError(
                "execution task_id or step_id does not match download request"
            )

        try:
            validate_execution_artifacts(request, execution)
        except ArtifactContractError as error:
            raise DownloadLifecycleIntegrityError(
                f"download execution artifacts are invalid: {error}"
            ) from error

        artifacts = [
            artifact
            for artifact in execution.artifacts
            if artifact.get("artifact_type") == QUARANTINED_DOWNLOAD_ARTIFACT
        ]
        if len(artifacts) != 1:
            raise DownloadLifecycleIntegrityError(
                "download execution requires exactly one quarantined_download artifact"
            )
        artifact = artifacts[0]

        if not await self._store.verify_integrity(request.request_id):
            raise DownloadLifecycleIntegrityError("quarantine failed integrity verification")
        try:
            record = await self._store.get(request.request_id)
        except QuarantineIntegrityError as error:
            raise DownloadLifecycleIntegrityError("quarantine record cannot be loaded") from error

        self._validate_record_correlation(request, record)
        self._validate_artifact_against_record(artifact, record)
        self._validate_pending_change(execution.pending_changes, record)
        return record

    @staticmethod
    def _validate_record_correlation(
        request: ToolCallRequest,
        record: QuarantineRecord,
    ) -> None:
        if record.task_id != request.task_id:
            raise DownloadLifecycleCorrelationError("quarantine task_id does not match request")
        if record.step_id != request.step_id:
            raise DownloadLifecycleCorrelationError("quarantine step_id does not match request")
        if record.request_id != request.request_id:
            raise DownloadLifecycleCorrelationError("quarantine request_id does not match request")
        if record.tool_name != request.tool_name:
            raise DownloadLifecycleCorrelationError("quarantine tool_name does not match request")

    @staticmethod
    def _validate_artifact_against_record(
        artifact: Mapping[str, Any],
        record: QuarantineRecord,
    ) -> None:
        expected: dict[str, object] = {
            "quarantine_path": record.quarantine_path,
            "source_url": record.source_url,
            "final_url": record.final_url,
            "content_type": record.content_type,
            "status": QuarantineStatus.QUARANTINED.value,
            "sha256": record.content_sha256,
            "size_bytes": record.size_bytes,
        }
        for field_name, expected_value in expected.items():
            if artifact.get(field_name) != expected_value:
                raise DownloadLifecycleIntegrityError(
                    f"quarantined_download artifact {field_name} does not match quarantine"
                )

    @staticmethod
    def _validate_pending_change(
        pending_changes: list[dict[str, Any]],
        record: QuarantineRecord,
    ) -> None:
        if len(pending_changes) != 1:
            raise DownloadLifecycleIntegrityError(
                "download execution requires exactly one pending change"
            )
        change = pending_changes[0]
        expected: dict[str, object] = {
            "operation": "DOWNLOAD",
            "quarantine_path": record.quarantine_path,
            "source_url": record.source_url,
            "final_url": record.final_url,
            "content_sha256": record.content_sha256,
            "size_bytes": record.size_bytes,
            "status": QuarantineStatus.QUARANTINED.value,
        }
        for field_name, expected_value in expected.items():
            if change.get(field_name) != expected_value:
                raise DownloadLifecycleIntegrityError(
                    f"pending download change {field_name} does not match quarantine"
                )
