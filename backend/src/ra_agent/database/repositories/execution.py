"""SQLAlchemy-backed repository for execution claim persistence."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ra_agent.database.models import ExecutionClaimRow


class SqliteExecutionClaimRepository:
    """Persistent metadata store for request execution claims."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def upsert_claim(
        self, request_id: str, fingerprint: str, *, resumable: bool = False
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(ExecutionClaimRow, request_id)
            if row is None:
                row = ExecutionClaimRow(
                    request_id=request_id,
                    fingerprint=fingerprint,
                    resumable=1 if resumable else 0,
                    status="IN_FLIGHT",
                )
                session.add(row)
            else:
                row.fingerprint = fingerprint
                row.resumable = 1 if resumable else 0
                row.status = "IN_FLIGHT"
            await session.commit()

    async def get_claim(self, request_id: str) -> ExecutionClaimRow | None:
        async with self._session_factory() as session:
            return await session.get(ExecutionClaimRow, request_id)

    async def mark_completed(self, request_id: str, *, resumable: bool = False) -> None:
        async with self._session_factory() as session:
            row = await session.get(ExecutionClaimRow, request_id)
            if row is not None:
                row.status = "WAITING_APPROVAL" if resumable else "COMPLETED"
                row.resumable = 1 if resumable else 0
                await session.commit()

    async def mark_failed(self, request_id: str) -> None:
        async with self._session_factory() as session:
            row = await session.get(ExecutionClaimRow, request_id)
            if row is not None:
                row.status = "FAILED"
                row.resumable = 0
                await session.commit()
