"""Persistent RequestExecutionRegistry — in-memory Future + SQLite durable metadata."""

import asyncio
from concurrent.futures import Future
from dataclasses import dataclass

from ra_agent.contracts import ExecutionStatus, ToolCallRequest, ToolExecutionResult
from ra_agent.runtime.idempotency import RequestExecutionClaim, request_fingerprint

from .repositories.execution import SqliteExecutionClaimRepository


@dataclass(slots=True)
class _PersistentEntry:
    fingerprint: str
    future: Future[ToolExecutionResult]
    resumable: bool = False


class PersistentRequestExecutionRegistry:
    """Durable registry backed by SQLite + in-memory Future coordination for idempotency.

    Futures remain in-process (cross-task coordination requires live objects).
    The database stores claim metadata for durability across restarts.
    """

    def __init__(self, *, repository: SqliteExecutionClaimRepository) -> None:
        self._repo = repository
        self._entries: dict[str, _PersistentEntry] = {}
        self._lock = asyncio.Lock()

    async def claim(self, request: ToolCallRequest) -> RequestExecutionClaim:
        fingerprint = request_fingerprint(request)
        async with self._lock:
            row, owns_execution = await self._repo.claim(request.request_id, fingerprint)
            if row.fingerprint != fingerprint:
                return RequestExecutionClaim(
                    request_id=request.request_id,
                    fingerprint=fingerprint,
                    owns_execution=False,
                    conflict=True,
                    future=None,
                )
            entry = self._entries.get(request.request_id)
            if entry is None:
                if not owns_execution:
                    return RequestExecutionClaim(
                        request_id=request.request_id,
                        fingerprint=fingerprint,
                        owns_execution=False,
                        conflict=False,
                        future=None,
                    )
                future: Future[ToolExecutionResult] = Future()
                entry = _PersistentEntry(fingerprint, future)
                self._entries[request.request_id] = entry
            if entry.fingerprint != fingerprint:
                return RequestExecutionClaim(
                    request_id=request.request_id,
                    fingerprint=fingerprint,
                    owns_execution=False,
                    conflict=True,
                    future=None,
                )
            return RequestExecutionClaim(
                request_id=request.request_id,
                fingerprint=fingerprint,
                owns_execution=owns_execution,
                conflict=False,
                future=entry.future,
            )

    async def wait(self, claim: RequestExecutionClaim) -> ToolExecutionResult:
        if claim.future is None or claim.conflict:
            raise ValueError("Cannot wait for a conflicting request claim")
        return await asyncio.shield(asyncio.wrap_future(claim.future))

    async def claim_resume(self, request: ToolCallRequest) -> RequestExecutionClaim:
        fingerprint = request_fingerprint(request)
        async with self._lock:
            row, owns_execution = await self._repo.claim_resume(request.request_id, fingerprint)
            if row is None:
                raise ValueError("Cannot resume a request that has not been scheduled")
            if row.fingerprint != fingerprint:
                return RequestExecutionClaim(
                    request_id=request.request_id,
                    fingerprint=fingerprint,
                    owns_execution=False,
                    conflict=True,
                    future=None,
                )
            entry = self._entries.get(request.request_id)
            if entry is None:
                if not owns_execution:
                    return RequestExecutionClaim(
                        request_id=request.request_id,
                        fingerprint=fingerprint,
                        owns_execution=False,
                        conflict=False,
                        future=None,
                    )
                entry = _PersistentEntry(fingerprint, Future())
                self._entries[request.request_id] = entry
            if owns_execution:
                entry.resumable = False
                entry.future = Future()
            return RequestExecutionClaim(
                request_id=request.request_id,
                fingerprint=fingerprint,
                owns_execution=owns_execution,
                conflict=False,
                future=entry.future,
            )

    async def complete(
        self, claim: RequestExecutionClaim, result: ToolExecutionResult
    ) -> None:
        if not claim.owns_execution or claim.future is None:
            raise ValueError("Only the execution owner can publish a result")
        async with self._lock:
            entry = self._entries.get(claim.request_id)
            if entry is None or entry.future is not claim.future:
                raise ValueError("Execution claim is no longer current")
            resumable = result.status is ExecutionStatus.WAITING_APPROVAL
            entry.resumable = resumable
            await self._repo.mark_completed(claim.request_id, resumable=resumable)
            claim.future.set_result(result)

    async def fail(self, claim: RequestExecutionClaim, error: BaseException) -> None:
        if not claim.owns_execution or claim.future is None:
            raise ValueError("Only the execution owner can publish a failure")
        async with self._lock:
            entry = self._entries.get(claim.request_id)
            if entry is None or entry.future is not claim.future:
                raise ValueError("Execution claim is no longer current")
            entry.resumable = False
            await self._repo.mark_failed(claim.request_id)
            claim.future.set_exception(error)
