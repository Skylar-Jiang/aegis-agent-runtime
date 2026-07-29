from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from ra_agent.contracts import (
    MemoryStatus,
    PostCheckResult,
    ToolCallRequest,
    ToolExecutionResult,
)
from ra_agent.memory import (
    MemoryLifecycleManager,
    MemoryNotFoundError,
)

from .download_manager import DownloadLifecycleManager
from .effect_manager import EffectManager
from .effect_store import EffectNotFoundError
from .pending_store import PendingStore
from .quarantine import QuarantineNotFoundError, QuarantineStatus
from .rollback import RollbackManager


class CleanupCoordinatorError(RuntimeError):
    """Base error raised by request-scoped cleanup coordination."""


class CleanupIncompleteError(CleanupCoordinatorError):
    """Raised when at least one cleanup action could not be completed."""

    def __init__(self, report: CleanupReport) -> None:
        self.report = report
        details = "; ".join(report.failures) or "unknown cleanup failure"
        super().__init__(f"cleanup incomplete for {report.request_id}: {details}")


@dataclass(frozen=True, slots=True)
class CleanupContext:
    """Correlation data required to clean resources owned by one tool request."""

    task_id: str
    step_id: str
    request_id: str
    tool_name: str
    checkpoint_id: str | None = None


@dataclass(frozen=True, slots=True)
class CleanupReport:
    """Deterministic report for one request-scoped cleanup attempt."""

    request_id: str
    reason: str
    completed: tuple[str, ...]
    skipped: tuple[str, ...]
    failures: tuple[str, ...]

    @property
    def succeeded(self) -> bool:
        return not self.failures


_CleanupResultT = TypeVar("_CleanupResultT")


async def run_cleanup_shielded(
    cleanup: Awaitable[_CleanupResultT],
) -> _CleanupResultT:
    """Finish cleanup even when the caller is already being cancelled."""

    cleanup_task = asyncio.ensure_future(cleanup)
    try:
        return await asyncio.shield(cleanup_task)
    except asyncio.CancelledError:
        return await cleanup_task


class RequestCleanupCoordinator:
    """Route abort, rejection and failed-commit cleanup by pending resource type.

    This class does not call tool handlers or implement security checks. Runtime owns
    orchestration and may call these methods after cancellation, exceptions, PostCheck
    rejection, or a failed commit.
    """

    _FILESYSTEM_TOOLS = frozenset({"write_file", "delete_file"})

    def __init__(
        self,
        *,
        pending_store: PendingStore | None = None,
        rollback_manager: RollbackManager | None = None,
        memory_manager: MemoryLifecycleManager | None = None,
        download_manager: DownloadLifecycleManager | None = None,
        effect_manager: EffectManager | None = None,
    ) -> None:
        self._pending_store = pending_store
        self._rollback_manager = rollback_manager
        self._memory_manager = memory_manager
        self._download_manager = download_manager
        self._effect_manager = effect_manager
        self._locks: dict[str, asyncio.Lock] = {}

    async def abort(
        self,
        context: CleanupContext,
        *,
        reason: str,
    ) -> CleanupReport:
        """Fail closed after cancellation, timeout, validation, I/O, or network errors."""

        normalized_reason = self._validate_reason(reason)
        async with self._lock_for(context.request_id):
            return await self._abort_locked(context, normalized_reason)

    async def reject(
        self,
        request: ToolCallRequest,
        execution: ToolExecutionResult,
        post_check: PostCheckResult,
        *,
        checkpoint_id: str | None = None,
    ) -> CleanupReport:
        """Apply a failing PostCheck decision without permitting a later commit."""

        if post_check.passed:
            raise ValueError("cleanup rejection requires a failing PostCheck result")
        context = CleanupContext(
            task_id=request.task_id,
            step_id=request.step_id,
            request_id=request.request_id,
            tool_name=request.tool_name,
            checkpoint_id=checkpoint_id or execution.checkpoint_id,
        )
        reason = self._validate_reason(post_check.reason)
        async with self._lock_for(request.request_id):
            return await self._reject_locked(
                context,
                request,
                execution,
                post_check,
                reason,
            )

    async def commit(
        self,
        request: ToolCallRequest,
        execution: ToolExecutionResult,
        post_check: PostCheckResult,
    ) -> str:
        """Commit a non-filesystem pending resource after its checks have passed."""

        if not post_check.passed:
            raise ValueError("cleanup commit requires a passing PostCheck result")
        async with self._lock_for(request.request_id):
            if request.tool_name == "memory_write":
                if self._memory_manager is None:
                    raise CleanupCoordinatorError("MemoryLifecycleManager is not configured")
                await self._memory_manager.commit(request, execution, post_check)
                return "memory_trusted"
            if request.tool_name == "download_url":
                if self._download_manager is None:
                    raise CleanupCoordinatorError("DownloadLifecycleManager is not configured")
                await self._download_manager.commit(request, execution, post_check)
                return "download_committed"
            raise CleanupCoordinatorError(
                f"no managed pending resource for tool: {request.tool_name}"
            )

    async def complete_filesystem_commit(
        self,
        context: CleanupContext,
    ) -> CleanupReport:
        """Remove the temporary pending record after a filesystem commit succeeds."""

        if context.tool_name not in self._FILESYSTEM_TOOLS:
            raise CleanupCoordinatorError(
                f"no filesystem pending resource for tool: {context.tool_name}"
            )

        pending_store = self._pending_store
        if pending_store is None:
            raise CleanupCoordinatorError("PendingStore is not configured")

        async with self._lock_for(context.request_id):
            completed: list[str] = []
            failures: list[str] = []

            await self._attempt(
                "pending_cleanup",
                lambda: pending_store.cleanup(context.request_id),
                completed,
                failures,
            )

            effect_manager = self._effect_manager
            if not failures and effect_manager is not None:
                try:
                    await effect_manager.mark_committed(
                        context.request_id,
                        checkpoint_id=context.checkpoint_id,
                    )
                except EffectNotFoundError:
                    pass
                except Exception as error:
                    failures.append(self._failure("effect_commit", error))
                else:
                    completed.append("effect_commit")

            return self._finish_report(
                context.request_id,
                "filesystem commit completed",
                completed,
                [],
                failures,
            )

    async def rollback_failed_commit(
        self,
        context: CleanupContext,
        *,
        reason: str,
    ) -> CleanupReport:
        """Rollback a resource that may have been committed before an exception."""

        normalized_reason = self._validate_reason(reason)
        async with self._lock_for(context.request_id):
            return await self._abort_locked(context, normalized_reason)

    async def _abort_locked(
        self,
        context: CleanupContext,
        reason: str,
    ) -> CleanupReport:
        completed: list[str] = []
        skipped: list[str] = []
        failures: list[str] = []

        if context.tool_name in self._FILESYSTEM_TOOLS:
            await self._cleanup_filesystem(
                context,
                completed=completed,
                skipped=skipped,
                failures=failures,
            )
        elif context.tool_name == "memory_write":
            await self._rollback_memory(
                context.request_id,
                reason=reason,
                completed=completed,
                skipped=skipped,
                failures=failures,
            )
        elif context.tool_name == "download_url":
            await self._rollback_download(
                context.request_id,
                reason=reason,
                completed=completed,
                skipped=skipped,
                failures=failures,
            )
        else:
            skipped.append("no_pending_resource")

        effect_manager = self._effect_manager
        if (
            not failures
            and effect_manager is not None
            and context.tool_name in self._FILESYSTEM_TOOLS
        ):
            if context.checkpoint_id is None:
                await self._attempt(
                    "effect_clean",
                    lambda: effect_manager.mark_cleaned(
                        context.request_id,
                        missing_ok=True,
                    ),
                    completed,
                    failures,
                )
            else:
                await self._attempt(
                    "effect_rollback",
                    lambda: effect_manager.mark_rolled_back(
                        context.request_id,
                        missing_ok=True,
                    ),
                    completed,
                    failures,
                )

        return self._finish_report(
            context.request_id,
            reason,
            completed,
            skipped,
            failures,
        )

    async def _reject_locked(
        self,
        context: CleanupContext,
        request: ToolCallRequest,
        execution: ToolExecutionResult,
        post_check: PostCheckResult,
        reason: str,
    ) -> CleanupReport:
        completed: list[str] = []
        skipped: list[str] = []
        failures: list[str] = []

        if context.tool_name in self._FILESYSTEM_TOOLS:
            await self._cleanup_filesystem(
                context,
                completed=completed,
                skipped=skipped,
                failures=failures,
            )
        elif context.tool_name == "memory_write":
            memory_manager = self._memory_manager
            if memory_manager is None:
                failures.append("memory_reject: MemoryLifecycleManager is not configured")
            else:
                await self._attempt(
                    "memory_reject",
                    lambda: memory_manager.reject(request, execution, post_check),
                    completed,
                    failures,
                )
                await self._attempt(
                    "memory_temp_cleanup",
                    lambda: memory_manager.store.cleanup(context.request_id),
                    completed,
                    failures,
                )
        elif context.tool_name == "download_url":
            download_manager = self._download_manager
            if download_manager is None:
                failures.append("download_reject: DownloadLifecycleManager is not configured")
            else:
                await self._attempt(
                    "download_reject",
                    lambda: download_manager.reject(request, execution, post_check),
                    completed,
                    failures,
                )
                await self._attempt(
                    "download_temp_cleanup",
                    lambda: download_manager.store.cleanup(context.request_id),
                    completed,
                    failures,
                )
        else:
            skipped.append("no_pending_resource")

        effect_manager = self._effect_manager
        if (
            not failures
            and effect_manager is not None
            and context.tool_name in self._FILESYSTEM_TOOLS
        ):
            await self._attempt(
                "effect_reject",
                lambda: effect_manager.mark_rejected(context.request_id),
                completed,
                failures,
            )

        return self._finish_report(
            context.request_id,
            reason,
            completed,
            skipped,
            failures,
        )

    async def _cleanup_filesystem(
        self,
        context: CleanupContext,
        *,
        completed: list[str],
        skipped: list[str],
        failures: list[str],
    ) -> None:
        if context.checkpoint_id is not None:
            rollback_manager = self._rollback_manager
            if rollback_manager is None:
                failures.append("filesystem_rollback: RollbackManager is not configured")
                return
            checkpoint_id = context.checkpoint_id
            succeeded = await self._attempt(
                "filesystem_rollback",
                lambda: rollback_manager.rollback(
                    checkpoint_id,
                    context.request_id,
                ),
                completed,
                failures,
            )
            if not succeeded:
                # Keep pending and checkpoint evidence when workspace recovery conflicts.
                return
        elif self._pending_store is None:
            failures.append("pending_cleanup: PendingStore is not configured")
            return
        else:
            skipped.append("filesystem_rollback_no_checkpoint")

        pending_store = self._pending_store
        if pending_store is not None:
            await self._attempt(
                "pending_cleanup",
                lambda: pending_store.cleanup(context.request_id),
                completed,
                failures,
            )

    async def _rollback_memory(
        self,
        request_id: str,
        *,
        reason: str,
        completed: list[str],
        skipped: list[str],
        failures: list[str],
    ) -> None:
        memory_manager = self._memory_manager
        if memory_manager is None:
            failures.append("memory_rollback: MemoryLifecycleManager is not configured")
            return

        try:
            record = await memory_manager.store.get(request_id)
        except MemoryNotFoundError:
            skipped.append("memory_record_not_found")
        except Exception as error:
            failures.append(self._failure("memory_load", error))
        else:
            if record.status in {MemoryStatus.PENDING, MemoryStatus.TRUSTED}:
                await self._attempt(
                    "memory_rollback",
                    lambda: memory_manager.rollback(request_id, reason=reason),
                    completed,
                    failures,
                )
            else:
                skipped.append(f"memory_already_{record.status.value.lower()}")

        await self._attempt(
            "memory_temp_cleanup",
            lambda: memory_manager.store.cleanup(request_id),
            completed,
            failures,
        )

    async def _rollback_download(
        self,
        request_id: str,
        *,
        reason: str,
        completed: list[str],
        skipped: list[str],
        failures: list[str],
    ) -> None:
        download_manager = self._download_manager
        if download_manager is None:
            failures.append("download_rollback: DownloadLifecycleManager is not configured")
            return

        try:
            record = await download_manager.store.get(request_id)
        except QuarantineNotFoundError:
            skipped.append("quarantine_record_not_found")
        except Exception as error:
            failures.append(self._failure("quarantine_load", error))
        else:
            if record.status in {
                QuarantineStatus.QUARANTINED,
                QuarantineStatus.COMMITTED,
            }:
                await self._attempt(
                    "download_rollback",
                    lambda: download_manager.rollback(request_id, reason=reason),
                    completed,
                    failures,
                )
            else:
                skipped.append(f"download_already_{record.status.value.lower()}")

        await self._attempt(
            "download_temp_cleanup",
            lambda: download_manager.store.cleanup(request_id),
            completed,
            failures,
        )

    @staticmethod
    async def _attempt(
        name: str,
        action: Callable[[], Awaitable[object]],
        completed: list[str],
        failures: list[str],
    ) -> bool:
        try:
            await action()
        except Exception as error:
            failures.append(RequestCleanupCoordinator._failure(name, error))
            return False
        completed.append(name)
        return True

    @staticmethod
    def _finish_report(
        request_id: str,
        reason: str,
        completed: list[str],
        skipped: list[str],
        failures: list[str],
    ) -> CleanupReport:
        report = CleanupReport(
            request_id=request_id,
            reason=reason,
            completed=tuple(completed),
            skipped=tuple(skipped),
            failures=tuple(failures),
        )
        if not report.succeeded:
            raise CleanupIncompleteError(report)
        return report

    def _lock_for(self, request_id: str) -> asyncio.Lock:
        return self._locks.setdefault(request_id, asyncio.Lock())

    @staticmethod
    def _validate_reason(reason: str) -> str:
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("cleanup reason must be a non-empty string")
        return reason.strip()

    @staticmethod
    def _failure(name: str, error: Exception) -> str:
        reason = str(error) or type(error).__name__
        return f"{name}: {type(error).__name__}: {reason}"
