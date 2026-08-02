from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from ra_agent.contracts import (
    EffectRecord,
    EffectStatus,
    ExecutionStatus,
    MemoryStatus,
    RollbackPlan,
    RollbackPlanResult,
)

from .artifacts import PENDING_DELETE_ARTIFACT, PENDING_FILE_ARTIFACT
from .checkpoint import (
    CheckpointRecord,
    CheckpointStatus,
    FilesystemCheckpointManager,
)
from .download_manager import DownloadLifecycleManager
from .effect_manager import (
    DOWNLOAD_EFFECT,
    FILE_DELETE_EFFECT,
    FILE_WRITE_EFFECT,
    MEMORY_WRITE_EFFECT,
    EffectManager,
)
from .effect_store import (
    EffectNotFoundError,
    FilesystemEffectStore,
)
from .pending_store import PendingOperation
from .quarantine import QuarantineRecord, QuarantineStatus
from .rollback import (
    CommittedEffectState,
    RollbackManager,
)

if TYPE_CHECKING:
    from ra_agent.memory import MemoryLifecycleManager
    from ra_agent.memory.models import MemoryRecord


class SelectiveRollbackError(RuntimeError):
    """Base error raised by graph-scoped selective rollback."""


class RollbackPlanValidationError(SelectiveRollbackError):
    """Raised when a rollback plan is unsafe or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class ResolvedRollbackEffect:
    """One validated effect and its optional filesystem checkpoint."""

    effect: EffectRecord
    checkpoint: CheckpointRecord | None = None
    committed_state: CommittedEffectState | None = None


_RollbackT = TypeVar("_RollbackT")


class SelectiveRollbackExecutor:
    """Execute only the effects explicitly named by a frozen v0.4 RollbackPlan.

    Preflight resolves and validates the complete scope before any state changes.
    Runtime owns plan creation; this component only validates and executes it.
    """

    _FILE_KINDS = frozenset({FILE_WRITE_EFFECT, FILE_DELETE_EFFECT})
    _ALLOWED_KINDS = frozenset(
        {
            FILE_WRITE_EFFECT,
            FILE_DELETE_EFFECT,
            MEMORY_WRITE_EFFECT,
            DOWNLOAD_EFFECT,
        }
    )

    def __init__(
        self,
        *,
        effect_store: FilesystemEffectStore,
        effect_manager: EffectManager,
        checkpoint_manager: FilesystemCheckpointManager | None = None,
        rollback_manager: RollbackManager | None = None,
        memory_manager: MemoryLifecycleManager | None = None,
        download_manager: DownloadLifecycleManager | None = None,
    ) -> None:
        self._effect_store = effect_store
        self._effect_manager = effect_manager
        self._checkpoint_manager = checkpoint_manager
        self._rollback_manager = rollback_manager
        self._memory_manager = memory_manager
        self._download_manager = download_manager
        self._target_locks: dict[str, asyncio.Lock] = {}
        self._target_locks_guard = asyncio.Lock()

    async def execute(self, plan: RollbackPlan) -> RollbackPlanResult:
        """Validate a plan fully, then rollback its explicit effects deterministically."""

        resolved = await self._resolve_scope(plan)
        groups: dict[str, list[ResolvedRollbackEffect]] = defaultdict(list)
        for item in resolved:
            groups[item.effect.target_ref].append(item)

        rolled_back: set[str] = set()
        failed: set[str] = set()
        failure_types: dict[str, str] = {}

        async with self._acquire_target_locks(tuple(groups)):
            for target_ref in sorted(groups):
                # A target's latest effect must be undone before an older effect.
                target_effects = sorted(
                    groups[target_ref],
                    key=lambda item: (item.effect.created_at, item.effect.effect_id),
                    reverse=True,
                )
                for index, item in enumerate(target_effects):
                    request_id = item.effect.request_id
                    try:
                        await self._rollback_one(item, reason=plan.reason)
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        failed.add(request_id)
                        failure_types[request_id] = type(error).__name__
                        # Older effects on the same target depend on the newer rollback.
                        for blocked in target_effects[index + 1 :]:
                            blocked_request_id = blocked.effect.request_id
                            failed.add(blocked_request_id)
                            failure_types.setdefault(
                                blocked_request_id,
                                "blocked_by_newer_effect_failure",
                            )
                        break
                    else:
                        rolled_back.add(request_id)

        rolled_back.difference_update(failed)
        status = ExecutionStatus.FAILED if failed else ExecutionStatus.SUCCESS
        reason = self._result_reason(plan, failed, failure_types)
        return RollbackPlanResult(
            plan_id=plan.plan_id,
            task_id=plan.task_id,
            rolled_back_request_ids=sorted(rolled_back),
            failed_request_ids=sorted(failed),
            status=status,
            reason=reason,
        )

    async def execute_rollback_plan(self, plan: RollbackPlan) -> RollbackPlanResult:
        """Implement the frozen V2 rollback Protocol without changing legacy callers."""

        return await self.execute(plan)

    async def _resolve_scope(
        self,
        plan: RollbackPlan,
    ) -> tuple[ResolvedRollbackEffect, ...]:
        task_effects = await self._effect_store.list_by_task_id(plan.task_id)
        if not task_effects:
            raise RollbackPlanValidationError(
                f"rollback task has no registered effects: {plan.task_id}"
            )

        task_effects_by_checkpoint: dict[str, list[EffectRecord]] = defaultdict(list)
        for effect in task_effects:
            if effect.checkpoint_id is not None:
                task_effects_by_checkpoint[effect.checkpoint_id].append(effect)

        selected: dict[str, EffectRecord] = {}

        for effect_id in plan.effect_ids:
            effect = await self._get_effect(effect_id)
            self._require_task(effect, plan.task_id, label="effect")
            selected[effect.effect_id] = effect

        for request_id in plan.request_ids:
            effect = await self._effect_store.get_by_request_id(request_id)
            if effect is None:
                raise RollbackPlanValidationError(
                    f"rollback request_id has no effect: {request_id}"
                )
            self._require_task(effect, plan.task_id, label="request")
            selected[effect.effect_id] = effect

        for checkpoint_id in plan.checkpoint_ids:
            checkpoint_manager = self._require_checkpoint_manager()
            checkpoint = await self._get_checkpoint(checkpoint_id)
            if checkpoint.task_id != plan.task_id:
                raise RollbackPlanValidationError(
                    f"checkpoint {checkpoint_id} belongs to another task"
                )
            if not await checkpoint_manager.verify_integrity(checkpoint_id):
                raise RollbackPlanValidationError(
                    f"checkpoint failed integrity verification: {checkpoint_id}"
                )
            matches = task_effects_by_checkpoint.get(checkpoint_id, [])
            if len(matches) != 1:
                raise RollbackPlanValidationError(
                    f"checkpoint must map to exactly one task effect: {checkpoint_id}"
                )
            selected[matches[0].effect_id] = matches[0]

        try:
            plan.validate_effect_scope(list(task_effects))
        except ValueError as error:
            raise RollbackPlanValidationError(str(error)) from error

        if not selected:
            raise RollbackPlanValidationError("rollback plan resolved to no effects")

        resolved: list[ResolvedRollbackEffect] = []
        for effect in selected.values():
            if not await self._effect_store.verify_integrity(effect.request_id):
                raise RollbackPlanValidationError(
                    f"effect failed integrity verification: {effect.effect_id}"
                )
            resolved.append(await self._validate_effect(effect, plan.task_id))

        return tuple(
            sorted(
                resolved,
                key=lambda item: (
                    item.effect.target_ref,
                    item.effect.created_at,
                    item.effect.effect_id,
                ),
            )
        )

    async def _validate_effect(
        self,
        effect: EffectRecord,
        task_id: str,
    ) -> ResolvedRollbackEffect:
        self._require_task(effect, task_id, label="effect")
        if effect.kind not in self._ALLOWED_KINDS:
            raise RollbackPlanValidationError(f"unsupported effect kind: {effect.kind}")
        if effect.status in {EffectStatus.REJECTED, EffectStatus.CLEANED}:
            raise RollbackPlanValidationError(
                f"effect status cannot be rolled back: {effect.status.value}"
            )

        if effect.kind in self._FILE_KINDS:
            return await self._validate_file_effect(effect)
        if effect.kind == MEMORY_WRITE_EFFECT:
            return await self._validate_memory_effect(effect)
        return await self._validate_download_effect(effect)

    async def _validate_file_effect(
        self,
        effect: EffectRecord,
    ) -> ResolvedRollbackEffect:
        if effect.status not in {EffectStatus.COMMITTED, EffectStatus.ROLLED_BACK}:
            raise RollbackPlanValidationError(
                "filesystem selective rollback requires COMMITTED or ROLLED_BACK effect"
            )
        if effect.checkpoint_id is None:
            raise RollbackPlanValidationError("filesystem effect is missing checkpoint_id")
        if self._rollback_manager is None:
            raise RollbackPlanValidationError("filesystem RollbackManager is not configured")

        checkpoint_manager = self._require_checkpoint_manager()
        checkpoint = await self._get_checkpoint(effect.checkpoint_id)
        if not await checkpoint_manager.verify_integrity(checkpoint.checkpoint_id):
            raise RollbackPlanValidationError(
                f"checkpoint failed integrity verification: {checkpoint.checkpoint_id}"
            )
        if (
            checkpoint.task_id != effect.task_id
            or checkpoint.step_id != effect.step_id
            or checkpoint.request_id != effect.request_id
            or checkpoint.checkpoint_id != effect.checkpoint_id
        ):
            raise RollbackPlanValidationError(
                "filesystem effect and checkpoint correlation does not match"
            )
        if len(checkpoint.target_paths) != 1:
            raise RollbackPlanValidationError(
                "filesystem effect requires exactly one checkpoint target"
            )
        if effect.target_ref != f"file:{checkpoint.target_paths[0]}":
            raise RollbackPlanValidationError(
                "filesystem effect target_ref does not match checkpoint target"
            )

        expected_tool = {
            FILE_WRITE_EFFECT: "write_file",
            FILE_DELETE_EFFECT: "delete_file",
        }[effect.kind]
        if checkpoint.tool_name != expected_tool:
            raise RollbackPlanValidationError(
                "filesystem effect kind does not match checkpoint tool"
            )

        if effect.status is EffectStatus.COMMITTED:
            if checkpoint.status is not CheckpointStatus.COMMITTED:
                raise RollbackPlanValidationError(
                    "COMMITTED filesystem effect requires COMMITTED checkpoint"
                )
        elif checkpoint.status is not CheckpointStatus.ROLLED_BACK:
            raise RollbackPlanValidationError(
                "ROLLED_BACK filesystem effect requires ROLLED_BACK checkpoint"
            )

        committed_state = self._committed_file_state(effect)
        return ResolvedRollbackEffect(
            effect=effect,
            checkpoint=checkpoint,
            committed_state=committed_state,
        )

    async def _validate_memory_effect(
        self,
        effect: EffectRecord,
    ) -> ResolvedRollbackEffect:
        memory_manager = self._memory_manager
        if memory_manager is None:
            raise RollbackPlanValidationError("MemoryLifecycleManager is not configured")
        if effect.checkpoint_id is not None:
            raise RollbackPlanValidationError("memory effect must not contain checkpoint_id")
        if not effect.target_ref.startswith("memory:") or len(effect.target_ref) <= 7:
            raise RollbackPlanValidationError("memory effect target_ref is invalid")
        if effect.status not in {
            EffectStatus.PENDING,
            EffectStatus.COMMITTED,
            EffectStatus.ROLLED_BACK,
        }:
            raise RollbackPlanValidationError(
                f"memory effect status cannot be rolled back: {effect.status.value}"
            )
        if not await memory_manager.store.verify_integrity(effect.request_id):
            raise RollbackPlanValidationError(
                f"memory failed integrity verification: {effect.request_id}"
            )
        record = await memory_manager.store.get(effect.request_id)
        self._validate_memory_record(effect, record)
        return ResolvedRollbackEffect(effect=effect)

    async def _validate_download_effect(
        self,
        effect: EffectRecord,
    ) -> ResolvedRollbackEffect:
        download_manager = self._download_manager
        if download_manager is None:
            raise RollbackPlanValidationError("DownloadLifecycleManager is not configured")
        if effect.checkpoint_id is not None:
            raise RollbackPlanValidationError("download effect must not contain checkpoint_id")
        if effect.target_ref != f"download:{effect.request_id}":
            raise RollbackPlanValidationError(
                "download effect target_ref does not match request_id"
            )
        if effect.status not in {
            EffectStatus.PENDING,
            EffectStatus.COMMITTED,
            EffectStatus.ROLLED_BACK,
        }:
            raise RollbackPlanValidationError(
                f"download effect status cannot be rolled back: {effect.status.value}"
            )
        if not await download_manager.store.verify_integrity(effect.request_id):
            raise RollbackPlanValidationError(
                f"download failed integrity verification: {effect.request_id}"
            )
        record = await download_manager.store.get(effect.request_id)
        self._validate_download_record(effect, record)
        return ResolvedRollbackEffect(effect=effect)

    async def _rollback_one(
        self,
        item: ResolvedRollbackEffect,
        *,
        reason: str,
    ) -> None:
        effect = item.effect
        if effect.status is EffectStatus.ROLLED_BACK:
            return

        if effect.kind in self._FILE_KINDS:
            checkpoint = item.checkpoint
            if checkpoint is None or item.committed_state is None:
                raise RollbackPlanValidationError("filesystem rollback resolution is incomplete")
            rollback_manager = self._rollback_manager
            if rollback_manager is None:
                raise RollbackPlanValidationError("filesystem RollbackManager is not configured")
            result = await self._shield_and_propagate_cancel(
                rollback_manager.rollback(
                    checkpoint.checkpoint_id,
                    effect.request_id,
                    committed_effect=item.committed_state,
                )
            )
            if result.status is not ExecutionStatus.ROLLED_BACK:
                raise SelectiveRollbackError(
                    f"filesystem rollback did not complete: {effect.request_id}"
                )
            await self._effect_manager.mark_rolled_back(effect.request_id)
        elif effect.kind == MEMORY_WRITE_EFFECT:
            memory_manager = self._memory_manager
            if memory_manager is None:
                raise RollbackPlanValidationError("MemoryLifecycleManager is not configured")
            record = await self._shield_and_propagate_cancel(
                memory_manager.rollback(effect.request_id, reason=reason)
            )
            if record.status is not MemoryStatus.ROLLED_BACK:
                raise SelectiveRollbackError(
                    f"memory rollback did not complete: {effect.request_id}"
                )
        else:
            download_manager = self._download_manager
            if download_manager is None:
                raise RollbackPlanValidationError("DownloadLifecycleManager is not configured")
            record = await self._shield_and_propagate_cancel(
                download_manager.rollback(effect.request_id, reason=reason)
            )
            if record.status is not QuarantineStatus.ROLLED_BACK:
                raise SelectiveRollbackError(
                    f"download rollback did not complete: {effect.request_id}"
                )

        updated = await self._effect_store.get(effect.effect_id)
        if updated.status is not EffectStatus.ROLLED_BACK:
            raise SelectiveRollbackError(
                f"effect fact was not marked ROLLED_BACK: {effect.request_id}"
            )

    async def _get_effect(self, effect_id: str) -> EffectRecord:
        try:
            return await self._effect_store.get(effect_id)
        except EffectNotFoundError as error:
            raise RollbackPlanValidationError(
                f"rollback effect_id does not exist: {effect_id}"
            ) from error

    async def _get_checkpoint(self, checkpoint_id: str) -> CheckpointRecord:
        checkpoint_manager = self._require_checkpoint_manager()
        try:
            return await checkpoint_manager.get(checkpoint_id)
        except Exception as error:
            raise RollbackPlanValidationError(
                f"rollback checkpoint_id does not exist or is invalid: {checkpoint_id}"
            ) from error

    def _require_checkpoint_manager(self) -> FilesystemCheckpointManager:
        checkpoint_manager = self._checkpoint_manager
        if checkpoint_manager is None:
            raise RollbackPlanValidationError("FilesystemCheckpointManager is not configured")
        return checkpoint_manager

    @staticmethod
    def _require_task(
        effect: EffectRecord,
        task_id: str,
        *,
        label: str,
    ) -> None:
        if effect.task_id != task_id:
            raise RollbackPlanValidationError(f"{label} {effect.effect_id} belongs to another task")

    @staticmethod
    def _validate_memory_record(
        effect: EffectRecord,
        record: MemoryRecord,
    ) -> None:
        if (
            record.task_id != effect.task_id
            or record.step_id != effect.step_id
            or record.request_id != effect.request_id
            or effect.target_ref != f"memory:{record.key}"
        ):
            raise RollbackPlanValidationError(
                "memory effect and persisted record correlation does not match"
            )
        expected_status = {
            EffectStatus.PENDING: MemoryStatus.PENDING,
            EffectStatus.COMMITTED: MemoryStatus.TRUSTED,
            EffectStatus.ROLLED_BACK: MemoryStatus.ROLLED_BACK,
        }[effect.status]
        if record.status is not expected_status:
            raise RollbackPlanValidationError(
                "memory effect status does not match persisted memory"
            )

    @staticmethod
    def _validate_download_record(
        effect: EffectRecord,
        record: QuarantineRecord,
    ) -> None:
        if (
            record.task_id != effect.task_id
            or record.step_id != effect.step_id
            or record.request_id != effect.request_id
            or record.tool_name != "download_url"
        ):
            raise RollbackPlanValidationError(
                "download effect and quarantine correlation does not match"
            )
        expected_status = {
            EffectStatus.PENDING: QuarantineStatus.QUARANTINED,
            EffectStatus.COMMITTED: QuarantineStatus.COMMITTED,
            EffectStatus.ROLLED_BACK: QuarantineStatus.ROLLED_BACK,
        }[effect.status]
        if record.status is not expected_status:
            raise RollbackPlanValidationError("download effect status does not match quarantine")

    @staticmethod
    def _committed_file_state(effect: EffectRecord) -> CommittedEffectState:
        if len(effect.artifact_refs) != 1:
            raise RollbackPlanValidationError(
                "filesystem effect requires exactly one artifact reference"
            )
        parts = effect.artifact_refs[0].split(":")
        if len(parts) != 5 or parts[0] != "artifact":
            raise RollbackPlanValidationError("filesystem effect artifact reference is invalid")
        _, request_id, artifact_type, digest, raw_size = parts
        if request_id != effect.request_id:
            raise RollbackPlanValidationError(
                "filesystem artifact reference request_id does not match effect"
            )

        expected_type = {
            FILE_WRITE_EFFECT: PENDING_FILE_ARTIFACT,
            FILE_DELETE_EFFECT: PENDING_DELETE_ARTIFACT,
        }[effect.kind]
        if artifact_type != expected_type:
            raise RollbackPlanValidationError("filesystem artifact type does not match effect kind")

        if effect.kind == FILE_DELETE_EFFECT:
            return CommittedEffectState(operation=PendingOperation.DELETE)

        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise RollbackPlanValidationError("filesystem write artifact SHA-256 is invalid")
        try:
            size_bytes = int(raw_size)
        except ValueError as error:
            raise RollbackPlanValidationError(
                "filesystem write artifact size is invalid"
            ) from error
        if size_bytes < 0 or str(size_bytes) != raw_size:
            raise RollbackPlanValidationError("filesystem write artifact size is invalid")
        return CommittedEffectState(
            operation=PendingOperation.WRITE,
            content_sha256=digest,
            size_bytes=size_bytes,
        )

    async def _target_lock(self, target_ref: str) -> asyncio.Lock:
        async with self._target_locks_guard:
            return self._target_locks.setdefault(target_ref, asyncio.Lock())

    @asynccontextmanager
    async def _acquire_target_locks(
        self,
        target_refs: tuple[str, ...],
    ) -> AsyncIterator[None]:
        locks: list[asyncio.Lock] = []
        try:
            for target_ref in sorted(set(target_refs)):
                lock = await self._target_lock(target_ref)
                await lock.acquire()
                locks.append(lock)
            yield
        finally:
            for lock in reversed(locks):
                lock.release()

    @staticmethod
    async def _shield_and_propagate_cancel(
        operation: Awaitable[_RollbackT],
    ) -> _RollbackT:
        task = asyncio.ensure_future(operation)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError as cancellation:
            try:
                await task
            except Exception as error:
                cancellation.add_note(
                    f"rollback operation also failed: {type(error).__name__}: {error}"
                )
            raise

    @staticmethod
    def _result_reason(
        plan: RollbackPlan,
        failed: set[str],
        failure_types: dict[str, str],
    ) -> str:
        if not failed:
            scope_count = len(
                set(plan.effect_ids) | set(plan.request_ids) | set(plan.checkpoint_ids)
            )
            return f"selective rollback completed for {scope_count} explicit scope identifiers"
        summaries = ", ".join(
            f"{request_id}:{failure_types[request_id]}" for request_id in sorted(failed)
        )
        return f"selective rollback completed with {len(failed)} failure(s): {summaries}"
