"""SQLAlchemy-backed repository for execution claim persistence."""

from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ra_agent.database.models import ExecutionClaimRow


class SqliteExecutionClaimRepository:
    """Persistent metadata store for request execution claims."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def claim(
        self, request_id: str, fingerprint: str, *, resumable: bool = False
    ) -> tuple[ExecutionClaimRow, bool]:
        """Create an execution claim once, returning the durable row and ownership."""
        async with self._session_factory() as session:
            result = await session.execute(
                insert(ExecutionClaimRow)
                .values(
                    request_id=request_id,
                    fingerprint=fingerprint,
                    resumable=1 if resumable else 0,
                    status="IN_FLIGHT",
                )
                .on_conflict_do_nothing(index_elements=["request_id"])
            )
            await session.commit()
            row = await session.get(ExecutionClaimRow, request_id)
            if row is None:
                raise RuntimeError("Execution claim was not persisted")
            return row, cast(CursorResult[Any], result).rowcount == 1

    async def claim_resume(
        self, request_id: str, fingerprint: str
    ) -> tuple[ExecutionClaimRow | None, bool]:
        """Atomically claim one persisted waiting approval for resumption."""
        async with self._session_factory() as session:
            result = await session.execute(
                update(ExecutionClaimRow)
                .where(
                    ExecutionClaimRow.request_id == request_id,
                    ExecutionClaimRow.fingerprint == fingerprint,
                    ExecutionClaimRow.status == "WAITING_APPROVAL",
                )
                .values(status="IN_FLIGHT", resumable=0)
            )
            await session.commit()
            row = await session.get(ExecutionClaimRow, request_id)
            return row, cast(CursorResult[Any], result).rowcount == 1

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
