"""Persistent RequestExecutionRegistry — in-memory Future + SQLite durable metadata."""

from concurrent.futures import Future
from dataclasses import dataclass
from threading import Lock

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
        self._lock = Lock()

    async def claim(self, request: ToolCallRequest) -> RequestExecutionClaim:
        fingerprint = request_fingerprint(request)
        with self._lock:
            entry = self._entries.get(request.request_id)
            if entry is None:
                future: Future[ToolExecutionResult] = Future()
                self._entries[request.request_id] = _PersistentEntry(fingerprint, future)
                await self._repo.upsert_claim(
                    request.request_id, fingerprint, resumable=False
                )
                return RequestExecutionClaim(
                    request_id=request.request_id,
                    fingerprint=fingerprint,
                    owns_execution=True,
                    conflict=False,
                    future=future,
                )
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
                owns_execution=False,
                conflict=False,
                future=entry.future,
            )

    async def wait(self, claim: RequestExecutionClaim) -> ToolExecutionResult:
        import asyncio

        if claim.future is None or claim.conflict:
            raise ValueError("Cannot wait for a conflicting request claim")
        return await asyncio.shield(asyncio.wrap_future(claim.future))

    async def claim_resume(self, request: ToolCallRequest) -> RequestExecutionClaim:
        fingerprint = request_fingerprint(request)
        with self._lock:
            entry = self._entries.get(request.request_id)
            if entry is None:
                raise ValueError("Cannot resume a request that has not been scheduled")
            if entry.fingerprint != fingerprint:
                return RequestExecutionClaim(
                    request_id=request.request_id,
                    fingerprint=fingerprint,
                    owns_execution=False,
                    conflict=True,
                    future=None,
                )
            if entry.resumable:
                entry.resumable = False
                entry.future = Future()
                await self._repo.upsert_claim(
                    request.request_id, fingerprint, resumable=False
                )
                return RequestExecutionClaim(
                    request_id=request.request_id,
                    fingerprint=fingerprint,
                    owns_execution=True,
                    conflict=False,
                    future=entry.future,
                )
            return RequestExecutionClaim(
                request_id=request.request_id,
                fingerprint=fingerprint,
                owns_execution=False,
                conflict=False,
                future=entry.future,
            )

    async def complete(
        self, claim: RequestExecutionClaim, result: ToolExecutionResult
    ) -> None:
        if not claim.owns_execution or claim.future is None:
            raise ValueError("Only the execution owner can publish a result")
        with self._lock:
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
        with self._lock:
            entry = self._entries.get(claim.request_id)
            if entry is None or entry.future is not claim.future:
                raise ValueError("Execution claim is no longer current")
            entry.resumable = False
            await self._repo.mark_failed(claim.request_id)
            claim.future.set_exception(error)
