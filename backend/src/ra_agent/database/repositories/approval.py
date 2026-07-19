"""SQLAlchemy-backed repository for ApprovalRequest and ApprovalDecision persistence."""

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

    # ── decision ─────────────────────────────────────────────

    async def save_decision(self, row: ApprovalDecisionRow) -> None:
        async with self._session_factory() as session:
            existing = await session.get(ApprovalDecisionRow, row.approval_id)
            if existing is not None:
                raise ValueError(f"Approval already decided: {row.approval_id}")
            session.add(row)
            await session.commit()

    async def get_decision(self, approval_id: str) -> ApprovalDecisionRow | None:
        async with self._session_factory() as session:
            return await session.get(ApprovalDecisionRow, approval_id)

    async def mark_consumed(self, approval_id: str) -> None:
        async with self._session_factory() as session:
            row = await session.get(ApprovalDecisionRow, approval_id)
            if row is None:
                raise ValueError(f"No decision for approval: {approval_id}")
            if row.consumed:
                raise ValueError(f"Approval already consumed: {approval_id}")
            row.consumed = 1
            await session.commit()

    async def is_consumed(self, approval_id: str) -> bool:
        async with self._session_factory() as session:
            row = await session.get(ApprovalDecisionRow, approval_id)
            return row is not None and bool(row.consumed)
