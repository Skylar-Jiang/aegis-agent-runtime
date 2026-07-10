from typing import Protocol

from .models import MemoryRecord


class MemoryRepository(Protocol):
    async def add(self, record: MemoryRecord) -> None: ...
