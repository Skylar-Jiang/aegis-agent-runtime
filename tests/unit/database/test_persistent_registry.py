"""Tests for PersistentRequestExecutionRegistry — idempotency + resume."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from ra_agent.contracts import ExecutionStatus, SourceType, ToolCallRequest, ToolExecutionResult
from ra_agent.database.models import Base
from ra_agent.database.persistent_registry import PersistentRequestExecutionRegistry
from ra_agent.database.repositories.execution import SqliteExecutionClaimRepository


_engines: list[AsyncEngine] = []


async def _init_db(
    database_url: str = "sqlite+aiosqlite:///:memory:",
) -> async_sessionmaker:
    engine = create_async_engine(database_url)
    _engines.append(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def dispose_engines() -> AsyncIterator[None]:
    yield
    while _engines:
        await _engines.pop().dispose()


def _make_registry(session_factory: async_sessionmaker) -> PersistentRequestExecutionRegistry:
    repo = SqliteExecutionClaimRepository(session_factory)
    return PersistentRequestExecutionRegistry(repository=repo)


def _make_request(
    request_id: str = "request-1",
    tool_name: str = "delete_file",
    arguments: dict | None = None,
) -> ToolCallRequest:
    return ToolCallRequest(
        task_id="task-1",
        step_id="step-1",
        request_id=request_id,
        tool_name=tool_name,
        arguments=arguments or {"path": "draft.txt"},
        objective="remove draft",
        context_summary="user requested deletion",
        source_type=SourceType.USER,
        requested_at=datetime.now(UTC),
    )


def _make_result(
    request: ToolCallRequest, status: ExecutionStatus
) -> ToolExecutionResult:
    return ToolExecutionResult(
        task_id=request.task_id,
        step_id=request.step_id,
        request_id=request.request_id,
        status=status,
    )


# ── claim ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_first_claim_owns_execution() -> None:
    sf = await _init_db()
    reg = _make_registry(sf)
    request = _make_request()

    claim = await reg.claim(request)
    assert claim.owns_execution is True
    assert claim.conflict is False
    assert claim.future is not None


@pytest.mark.asyncio
async def test_duplicate_claim_does_not_own_execution() -> None:
    sf = await _init_db()
    reg = _make_registry(sf)
    request = _make_request()

    first = await reg.claim(request)
    second = await reg.claim(request)

    assert first.owns_execution is True
    assert second.owns_execution is False
    assert second.conflict is False
    assert second.future is first.future


@pytest.mark.asyncio
async def test_claim_with_different_args_is_conflict() -> None:
    sf = await _init_db()
    reg = _make_registry(sf)
    r1 = _make_request(request_id="req-1", arguments={"path": "a.txt"})
    r2 = _make_request(request_id="req-1", arguments={"path": "b.txt"})

    first = await reg.claim(r1)
    second = await reg.claim(r2)

    assert first.owns_execution is True
    assert second.conflict is True
    assert second.future is None


# ── complete / wait ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_complete_and_wait_returns_result() -> None:
    sf = await _init_db()
    reg = _make_registry(sf)
    request = _make_request()

    claim = await reg.claim(request)
    result = _make_result(request, ExecutionStatus.COMMITTED)
    await reg.complete(claim, result)

    waiter = await reg.claim(request)
    assert await reg.wait(waiter) == result


@pytest.mark.asyncio
async def test_only_owner_can_complete() -> None:
    sf = await _init_db()
    reg = _make_registry(sf)
    request = _make_request()

    first = await reg.claim(request)
    second = await reg.claim(request)
    assert first.owns_execution is True
    assert second.owns_execution is False

    with pytest.raises(ValueError, match="execution owner"):
        await reg.complete(second, _make_result(request, ExecutionStatus.COMMITTED))


# ── claim_resume (approval path) ──────────────────────────────

@pytest.mark.asyncio
async def test_waiting_result_can_be_resumed() -> None:
    sf = await _init_db()
    reg = _make_registry(sf)
    request = _make_request()

    initial = await reg.claim(request)
    await reg.complete(initial, _make_result(request, ExecutionStatus.WAITING_APPROVAL))

    resume_owner = await reg.claim_resume(request)
    assert resume_owner.owns_execution is True
    assert resume_owner.conflict is False

    final = _make_result(request, ExecutionStatus.COMMITTED)
    await reg.complete(resume_owner, final)

    waiter = await reg.claim(request)
    assert await reg.wait(waiter) == final


@pytest.mark.asyncio
async def test_terminal_result_cannot_be_resumed() -> None:
    sf = await _init_db()
    reg = _make_registry(sf)
    request = _make_request()

    claim = await reg.claim(request)
    await reg.complete(claim, _make_result(request, ExecutionStatus.BLOCKED))

    resumed = await reg.claim_resume(request)
    assert resumed.owns_execution is False


@pytest.mark.asyncio
async def test_claim_resume_unknown_request_raises() -> None:
    sf = await _init_db()
    reg = _make_registry(sf)

    with pytest.raises(ValueError, match="Cannot resume"):
        await reg.claim_resume(_make_request(request_id="never-scheduled"))


# ── fail ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fail_sets_exception_on_future() -> None:
    sf = await _init_db()
    reg = _make_registry(sf)
    request = _make_request()

    claim = await reg.claim(request)
    error = RuntimeError("test error")
    await reg.fail(claim, error)

    waiter = await reg.claim(request)
    with pytest.raises(RuntimeError, match="test error"):
        await reg.wait(waiter)


# ── persistence across recreation ─────────────────────────────

@pytest.mark.asyncio
async def test_claim_metadata_survives_recreation() -> None:
    sf = await _init_db()
    reg1 = _make_registry(sf)
    request = _make_request()

    claim1 = await reg1.claim(request)
    result = _make_result(request, ExecutionStatus.COMMITTED)
    await reg1.complete(claim1, result)

    # New registry with same DB — the future won't transfer,
    # but the new claim should know this request_id was used
    reg2 = _make_registry(sf)
    # A stored request must never be re-owned merely because the process restarted.
    claim2 = await reg2.claim(request)
    assert claim2.owns_execution is False
    assert claim2.future is None

    with pytest.raises(ValueError, match="Cannot wait"):
        await reg2.wait(claim2)


@pytest.mark.asyncio
async def test_recreated_registry_rejects_changed_request_fingerprint() -> None:
    sf = await _init_db()
    request = _make_request(request_id="recreated-request", arguments={"path": "a.txt"})
    await _make_registry(sf).claim(request)

    changed_request = _make_request(
        request_id="recreated-request", arguments={"path": "b.txt"}
    )
    claim = await _make_registry(sf).claim(changed_request)

    assert claim.owns_execution is False
    assert claim.conflict is True
    assert claim.future is None


@pytest.mark.asyncio
async def test_concurrent_claims_have_one_owner_without_blocking_event_loop(
    tmp_path: Path,
) -> None:
    sf = await _init_db(f"sqlite+aiosqlite:///{(tmp_path / 'registry.db').as_posix()}")
    registry = _make_registry(sf)
    request = _make_request(request_id="concurrent-request")

    claims = await asyncio.wait_for(
        asyncio.gather(*(registry.claim(request) for _ in range(2))), timeout=1
    )

    assert sum(claim.owns_execution for claim in claims) == 1
    assert all(not claim.conflict for claim in claims)
