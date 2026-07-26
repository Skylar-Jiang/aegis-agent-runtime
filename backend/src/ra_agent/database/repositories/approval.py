"""SQLAlchemy-backed repository for ApprovalRequest and ApprovalDecision persistence."""

from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ra_agent.database.models import ApprovalDecisionRow, ApprovalRequestRow


class SqliteApprovalRepository:
    """Persistent CRUD for approval requests and decisions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # ── request ──────────────────────────────────────────────

    async def create_request(self, row: ApprovalRequestRow) -> None:
        async with self._session_factory() as session:
            existing = await session.get(ApprovalRequestRow, row.approval_id)
            if existing is not None:
                raise ValueError(f"Approval already exists: {row.approval_id}")
            session.add(row)
            await session.commit()

    async def get_request(self, approval_id: str) -> ApprovalRequestRow | None:
        async with self._session_factory() as session:
            return await session.get(ApprovalRequestRow, approval_id)

    async def list_requests_for_task(self, task_id: str) -> list[ApprovalRequestRow]:
        async with self._session_factory() as session:
            result = await session.scalars(
                select(ApprovalRequestRow)
                .where(ApprovalRequestRow.task_id == task_id)
                .order_by(ApprovalRequestRow.requested_at)
            )
            return list(result)

    # ── decision ─────────────────────────────────────────────

    async def save_decision(self, row: ApprovalDecisionRow) -> None:
        async with self._session_factory() as session:
            statement = insert(ApprovalDecisionRow).values(
                approval_id=row.approval_id,
                task_id=row.task_id,
                step_id=row.step_id,
                request_id=row.request_id,
                status=row.status,
                decided_by=row.decided_by,
                decided_at=row.decided_at,
                reason=row.reason,
                consumed=row.consumed,
            ).on_conflict_do_nothing(index_elements=[ApprovalDecisionRow.approval_id])
            result = await session.execute(statement)
            await session.commit()
            if cast(CursorResult[Any], result).rowcount != 1:
                raise ValueError(f"Approval already decided: {row.approval_id}")

    async def get_decision(self, approval_id: str) -> ApprovalDecisionRow | None:
        async with self._session_factory() as session:
            return await session.get(ApprovalDecisionRow, approval_id)

    async def consume_once(self, approval_id: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                update(ApprovalDecisionRow)
                .where(
                    ApprovalDecisionRow.approval_id == approval_id,
                    ApprovalDecisionRow.consumed == 0,
                )
                .values(consumed=1)
            )
            await session.commit()
            if cast(CursorResult[Any], result).rowcount != 1:
                raise ValueError(f"Approval already consumed: {approval_id}")

    async def is_consumed(self, approval_id: str) -> bool:
        async with self._session_factory() as session:
            row = await session.get(ApprovalDecisionRow, approval_id)
            return row is not None and bool(row.consumed)
