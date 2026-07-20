from typing import Any, Protocol

from ra_agent.contracts import AuditEvent


class AuditRepository(Protocol):
    async def append_with_next_sequence(self, event: AuditEvent) -> AuditEvent: ...

    async def list_for_task(
        self, task_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]: ...
