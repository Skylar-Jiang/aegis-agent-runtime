"""Durable task snapshots used by the Agent workbench and approval resume flow."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import CursorResult, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .models import AgentTaskRow


class TaskStore(Protocol):
    async def create(self, payload: dict[str, Any]) -> None: ...

    async def update(self, task_id: str, **changes: object) -> None: ...

    async def get(self, task_id: str) -> dict[str, Any] | None: ...

    async def list(self, *, limit: int = 50) -> list[dict[str, Any]]: ...

    async def mark_orphaned_running_tasks(self) -> int: ...


def _copy(payload: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(payload)


class InMemoryTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def create(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            self._tasks[payload["task_id"]] = _copy(payload)

    async def update(self, task_id: str, **changes: object) -> None:
        async with self._lock:
            if task_id not in self._tasks:
                raise KeyError(task_id)
            self._tasks[task_id].update(deepcopy(changes))
            self._tasks[task_id]["updated_at"] = datetime.now(UTC).isoformat()

    async def get(self, task_id: str) -> dict[str, Any] | None:
        async with self._lock:
            payload = self._tasks.get(task_id)
            return _copy(payload) if payload is not None else None

    async def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        async with self._lock:
            rows = sorted(
                self._tasks.values(), key=lambda item: str(item["created_at"]), reverse=True
            )
            return [_copy(item) for item in rows[:limit]]

    async def mark_orphaned_running_tasks(self) -> int:
        async with self._lock:
            changed = 0
            for task in self._tasks.values():
                if task["status"] in {"PLANNING", "RUNNING"}:
                    task["status"] = "INTERRUPTED"
                    task["updated_at"] = datetime.now(UTC).isoformat()
                    changed += 1
            return changed


class PersistentTaskStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine, expire_on_commit=False
        )

    async def create(self, payload: dict[str, Any]) -> None:
        async with self._sessions() as session:
            session.add(AgentTaskRow(**payload))
            await session.commit()

    async def update(self, task_id: str, **changes: object) -> None:
        values = {**changes, "updated_at": datetime.now(UTC).isoformat()}
        async with self._sessions() as session:
            result = await session.execute(
                update(AgentTaskRow).where(AgentTaskRow.task_id == task_id).values(**values)
            )
            if not isinstance(result, CursorResult) or result.rowcount != 1:
                await session.rollback()
                raise KeyError(task_id)
            await session.commit()

    async def get(self, task_id: str) -> dict[str, Any] | None:
        async with self._sessions() as session:
            row = await session.get(AgentTaskRow, task_id)
            return self._view(row) if row is not None else None

    async def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(AgentTaskRow).order_by(AgentTaskRow.created_at.desc()).limit(limit)
                )
            ).all()
            return [self._view(row) for row in rows]

    async def mark_orphaned_running_tasks(self) -> int:
        async with self._sessions() as session:
            result = await session.execute(
                update(AgentTaskRow)
                .where(AgentTaskRow.status.in_(("PLANNING", "RUNNING")))
                .values(status="INTERRUPTED", updated_at=datetime.now(UTC).isoformat())
            )
            await session.commit()
            return int(result.rowcount or 0) if isinstance(result, CursorResult) else 0

    @staticmethod
    def _view(row: AgentTaskRow) -> dict[str, Any]:
        return {
            "task_id": row.task_id,
            "objective": row.objective,
            "status": row.status,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "final_answer": row.final_answer,
            "contract": deepcopy(row.contract),
            "agent_state": deepcopy(row.agent_state),
            "conversation_id": row.conversation_id,
            "security_profile_id": row.security_profile_id,
            "security_profile_version": row.security_profile_version,
        }
