from typing import Any, Protocol

from ra_agent.contracts import AuditEvent


class AuditRepository(Protocol):
    async def append(self, event: AuditEvent) -> None: ...

    async def count_for_task(self, task_id: str) -> int: ...

    async def list_for_task(
        self, task_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]: ...
