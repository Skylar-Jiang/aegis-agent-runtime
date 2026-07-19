"""Tests for PersistentApprovalService — mirrors MockApprovalService semantics."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ra_agent.contracts import ApprovalRequest, ApprovalStatus
from ra_agent.database.models import Base
from ra_agent.database.repositories.approval import SqliteApprovalRepository
from ra_agent.security.approval_service import PersistentApprovalService


async def _init_db() -> async_sessionmaker:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)


def _make_service(session_factory: async_sessionmaker) -> PersistentApprovalService:
    repo = SqliteApprovalRepository(session_factory)
    return PersistentApprovalService(repository=repo)


def _make_approval(**overrides: object) -> ApprovalRequest:
    now = datetime.now(UTC)
    defaults = {
        "approval_id": "approval-1",
        "task_id": "task-1",
        "step_id": "step-1",
        "request_id": "request-1",
        "tool_name": "delete_file",
        "request_fingerprint": "fingerprint-1",
        "reason": "review required",
        "requested_at": now,
        "expires_at": now + timedelta(minutes=10),
    }
    defaults.update(overrides)  # type: ignore[arg-type]
    return ApprovalRequest(**defaults)  # type: ignore[arg-type]


# ── create ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_stores_and_returns_approval() -> None:
    sf = await _init_db()
    svc = _make_service(sf)
    approval = _make_approval()

    result = await svc.create(approval)
    assert result.approval_id == approval.approval_id
    assert result.status == ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_create_duplicate_raises() -> None:
    sf = await _init_db()
    svc = _make_service(sf)
    await svc.create(_make_approval())

    with pytest.raises(ValueError, match="already exists"):
        await svc.create(_make_approval())


# ── get_request ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_request_returns_stored() -> None:
    sf = await _init_db()
    svc = _make_service(sf)
    await svc.create(_make_approval())

    req = await svc.get_request("approval-1")
    assert req.approval_id == "approval-1"
    assert req.tool_name == "delete_file"


@pytest.mark.asyncio
async def test_get_request_unknown_raises() -> None:
    sf = await _init_db()
    svc = _make_service(sf)

    with pytest.raises(KeyError, match="Unknown approval"):
        await svc.get_request("nonexistent")


# ── grant / deny ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_grant_creates_decision() -> None:
    sf = await _init_db()
    svc = _make_service(sf)
    await svc.create(_make_approval())

    decision = await svc.grant("approval-1", "reviewer", "approved")
    assert decision.status is ApprovalStatus.GRANTED
    assert decision.decided_by == "reviewer"


@pytest.mark.asyncio
async def test_deny_creates_decision() -> None:
    sf = await _init_db()
    svc = _make_service(sf)
    await svc.create(_make_approval())

    decision = await svc.deny("approval-1", "reviewer", "blocked")
    assert decision.status is ApprovalStatus.DENIED


@pytest.mark.asyncio
async def test_cannot_grant_twice() -> None:
    sf = await _init_db()
    svc = _make_service(sf)
    await svc.create(_make_approval())
    await svc.grant("approval-1", "reviewer", "ok")

    with pytest.raises(ValueError, match="already decided"):
        await svc.grant("approval-1", "other", "double grant")


@pytest.mark.asyncio
async def test_cannot_grant_unknown() -> None:
    sf = await _init_db()
    svc = _make_service(sf)

    with pytest.raises(KeyError, match="Unknown approval"):
        await svc.grant("missing", "reviewer", "nope")


# ── consume ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_consume_after_grant_returns_decision_once() -> None:
    sf = await _init_db()
    svc = _make_service(sf)
    await svc.create(_make_approval())
    decision = await svc.grant("approval-1", "reviewer", "ok")

    consumed = await svc.consume("approval-1")
    assert consumed.approval_id == decision.approval_id

    with pytest.raises(ValueError, match="already consumed"):
        await svc.consume("approval-1")


@pytest.mark.asyncio
async def test_cannot_consume_pending_approval() -> None:
    sf = await _init_db()
    svc = _make_service(sf)
    await svc.create(_make_approval())

    with pytest.raises(ValueError, match="still pending"):
        await svc.consume("approval-1")


# ── expiry ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_expired_approval_auto_detected_on_get_decision() -> None:
    sf = await _init_db()
    svc = _make_service(sf)
    now = datetime.now(UTC)
    approval = _make_approval(
        approval_id="approval-expired",
        requested_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(minutes=1),
    )
    await svc.create(approval)

    decision = await svc.get_decision("approval-expired")
    assert decision is not None
    assert decision.status is ApprovalStatus.EXPIRED


@pytest.mark.asyncio
async def test_cannot_grant_expired_approval() -> None:
    sf = await _init_db()
    svc = _make_service(sf)
    now = datetime.now(UTC)
    approval = _make_approval(
        approval_id="approval-expired-grant",
        requested_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(minutes=1),
    )
    await svc.create(approval)

    with pytest.raises(ValueError, match="already decided"):
        await svc.grant("approval-expired-grant", "reviewer", "too late")


@pytest.mark.asyncio
async def test_expire_method_creates_expired_decision() -> None:
    sf = await _init_db()
    svc = _make_service(sf)
    await svc.create(_make_approval())

    decision = await svc.expire("approval-1")
    assert decision.status is ApprovalStatus.EXPIRED
    assert decision.decided_by == "system"


# ── persistence across recreation ─────────────────────────────

@pytest.mark.asyncio
async def test_decisions_survive_service_recreation() -> None:
    sf = await _init_db()
    svc1 = _make_service(sf)
    await svc1.create(_make_approval())
    await svc1.grant("approval-1", "reviewer", "ok")

    # New service with same DB
    svc2 = _make_service(sf)
    decision = await svc2.get_decision("approval-1")
    assert decision is not None
    assert decision.status is ApprovalStatus.GRANTED


@pytest.mark.asyncio
async def test_consumed_flag_survives_recreation() -> None:
    sf = await _init_db()
    svc1 = _make_service(sf)
    await svc1.create(_make_approval())
    await svc1.grant("approval-1", "reviewer", "ok")
    await svc1.consume("approval-1")

    svc2 = _make_service(sf)
    with pytest.raises(ValueError, match="already consumed"):
        await svc2.consume("approval-1")
