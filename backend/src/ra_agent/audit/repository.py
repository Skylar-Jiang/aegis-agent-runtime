from typing import Protocol

from ra_agent.contracts import AuditEvent


class AuditRepository(Protocol):
    async def append(self, event: AuditEvent) -> None: ...
