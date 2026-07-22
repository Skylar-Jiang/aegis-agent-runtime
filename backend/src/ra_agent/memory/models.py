from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from ra_agent.contracts import MemoryStatus


class MemoryRecord(BaseModel):
    """Durable metadata for one versioned runtime-memory write."""

    model_config = ConfigDict(frozen=True)

    memory_id: str
    key: str
    task_id: str
    step_id: str
    request_id: str
    tool_name: str
    payload_path: str
    content_sha256: str
    size_bytes: int
    status: MemoryStatus
    created_at: datetime
    updated_at: datetime
    trusted_at: datetime | None = None
    rejected_reason: str | None = None
    rollback_reason: str | None = None
