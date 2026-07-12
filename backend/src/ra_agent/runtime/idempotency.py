import asyncio
import json
from concurrent.futures import Future
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from typing import Protocol

from ra_agent.contracts import ToolCallRequest, ToolExecutionResult


def request_fingerprint(request: ToolCallRequest) -> str:
    payload = request.model_dump(
        mode="json",
        include={
            "task_id",
            "step_id",
            "tool_name",
            "arguments",
            "objective",
            "context_summary",
            "source_type",
        },
    )
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RequestExecutionClaim:
    request_id: str
    fingerprint: str
    owns_execution: bool
    conflict: bool
    future: Future[ToolExecutionResult] | None


class RequestExecutionRegistry(Protocol):
    async def claim(self, request: ToolCallRequest) -> RequestExecutionClaim: ...

    async def wait(self, claim: RequestExecutionClaim) -> ToolExecutionResult: ...

    async def complete(self, claim: RequestExecutionClaim, result: ToolExecutionResult) -> None: ...

    async def fail(self, claim: RequestExecutionClaim, error: BaseException) -> None: ...


@dataclass(slots=True)
class _RequestEntry:
    fingerprint: str
    future: Future[ToolExecutionResult]


class InMemoryRequestExecutionRegistry:
    """Thread-safe in-memory request registry for the single-process runtime."""

    def __init__(self) -> None:
        self._entries: dict[str, _RequestEntry] = {}
        self._lock = Lock()

    async def claim(self, request: ToolCallRequest) -> RequestExecutionClaim:
        fingerprint = request_fingerprint(request)
        with self._lock:
            entry = self._entries.get(request.request_id)
            if entry is None:
                future: Future[ToolExecutionResult] = Future()
                self._entries[request.request_id] = _RequestEntry(fingerprint, future)
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
        if claim.future is None or claim.conflict:
            raise ValueError("Cannot wait for a conflicting request claim")
        return await asyncio.shield(asyncio.wrap_future(claim.future))

    async def complete(self, claim: RequestExecutionClaim, result: ToolExecutionResult) -> None:
        if not claim.owns_execution or claim.future is None:
            raise ValueError("Only the execution owner can publish a result")
        claim.future.set_result(result)

    async def fail(self, claim: RequestExecutionClaim, error: BaseException) -> None:
        if not claim.owns_execution or claim.future is None:
            raise ValueError("Only the execution owner can publish a failure")
        claim.future.set_exception(error)
