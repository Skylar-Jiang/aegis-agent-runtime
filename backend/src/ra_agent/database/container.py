"""Persistent ServiceContainer factory — swaps in database-backed services."""

from dataclasses import replace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ra_agent.audit.event_bus import PersistentAuditRecorder
from ra_agent.core.bootstrap import build_mock_container
from ra_agent.core.container import ServiceContainer
from ra_agent.database.persistent_registry import PersistentRequestExecutionRegistry
from ra_agent.database.repositories.approval import SqliteApprovalRepository
from ra_agent.database.repositories.audit import SqliteAuditRepository
from ra_agent.database.repositories.execution import SqliteExecutionClaimRepository
from ra_agent.database.session import create_engine, create_session_factory
from ra_agent.security.approval_service import PersistentApprovalService


def build_persistent_container(
    database_url: str = "sqlite+aiosqlite:///./ra_agent.db",
) -> ServiceContainer:
    """Build a ServiceContainer with persistent SQLite-backed services.

    Only replaces the three slots owned by Member D:
    - audit_recorder (InMemoryAuditRecorder → PersistentAuditRecorder)
    - approval_service (MockApprovalService → PersistentApprovalService)
    - request_registry (InMemoryRequestExecutionRegistry → PersistentRequestExecutionRegistry)

    All other slots remain as mock implementations (owned by other members).
    """
    engine = create_engine(database_url)
    session_factory: async_sessionmaker[AsyncSession] = create_session_factory(engine)

    audit_repo = SqliteAuditRepository(session_factory)
    approval_repo = SqliteApprovalRepository(session_factory)
    execution_repo = SqliteExecutionClaimRepository(session_factory)

    return replace(
        build_mock_container(),
        audit_recorder=PersistentAuditRecorder(repository=audit_repo),
        approval_service=PersistentApprovalService(repository=approval_repo),
        request_registry=PersistentRequestExecutionRegistry(repository=execution_repo),
        database_engine=engine,
    )
