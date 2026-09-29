from __future__ import annotations

from typing import Protocol

from ra_agent.contracts import ToolCallRequest

from .models import MemoryRecord


class MemoryRepository(Protocol):
    """Internal storage boundary for versioned trusted runtime memory."""

    async def stage(
        self,
        request: ToolCallRequest,
        *,
        key: str,
        value: object,
    ) -> MemoryRecord: ...

    async def get(self, request_id: str) -> MemoryRecord: ...

    async def get_trusted(self, key: str) -> MemoryRecord | None: ...

    async def get_trusted_value(self, key: str) -> tuple[MemoryRecord, object] | None: ...

    async def read_value(self, request_id: str) -> object: ...

    async def list_pending(self) -> tuple[MemoryRecord, ...]: ...

    async def verify_integrity(self, request_id: str) -> bool: ...

    async def mark_trusted(self, request_id: str) -> MemoryRecord: ...

    async def mark_rejected(self, request_id: str, *, reason: str) -> MemoryRecord: ...

    async def mark_rolled_back(self, request_id: str, *, reason: str) -> MemoryRecord: ...

    async def cleanup(self, request_id: str) -> None: ...
