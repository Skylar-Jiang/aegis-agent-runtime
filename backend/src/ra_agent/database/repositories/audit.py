"""SQLAlchemy-backed AuditRepository implementation."""

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ra_agent.audit.repository import AuditRepository
from ra_agent.contracts import AuditEvent
from ra_agent.database.models import AuditEventRow, AuditTaskSequenceRow


class SqliteAuditRepository(AuditRepository):
    """Persists AuditEvent rows via SQLAlchemy async session factory."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def append_with_next_sequence(self, event: AuditEvent) -> AuditEvent:
        sequence_insert = insert(AuditTaskSequenceRow).values(
            task_id=event.task_id,
            next_sequence_number=2,
        )
        sequence_upsert = sequence_insert.on_conflict_do_update(
            index_elements=[AuditTaskSequenceRow.task_id],
            set_={"next_sequence_number": AuditTaskSequenceRow.next_sequence_number + 1},
        ).returning(AuditTaskSequenceRow.next_sequence_number)
        row = AuditEventRow(
            event_id=event.event_id,
            task_id=event.task_id,
            step_id=event.step_id,
            request_id=event.request_id,
            sequence_number=event.sequence_number,
            event_type=event.event_type.value,
            timestamp=event.timestamp.isoformat(),
            actor=event.actor,
            status=event.status,
            risk_level=event.risk_level.value if event.risk_level else None,
            decision=event.decision.value if event.decision else None,
            summary=event.summary,
            details=event.details,
        )
        async with self._session_factory() as session:
            next_sequence_number = (await session.execute(sequence_upsert)).scalar_one()
            event = event.model_copy(update={"sequence_number": next_sequence_number - 1})
            row.sequence_number = event.sequence_number
            session.add(row)
            await session.commit()
            return event

    async def list_for_task(
        self, task_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, object]]:
        async with self._session_factory() as session:
            stmt = (
                select(AuditEventRow)
                .where(AuditEventRow.task_id == task_id)
                .order_by(AuditEventRow.sequence_number.asc())
                .limit(limit)
                .offset(offset)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                {
                    "event_id": r.event_id,
                    "task_id": r.task_id,
                    "step_id": r.step_id,
                    "request_id": r.request_id,
                    "sequence_number": r.sequence_number,
                    "event_type": r.event_type,
                    "timestamp": r.timestamp,
                    "actor": r.actor,
                    "status": r.status,
                    "risk_level": r.risk_level,
                    "decision": r.decision,
                    "summary": r.summary,
                    "details": r.details,
                }
                for r in rows
            ]
