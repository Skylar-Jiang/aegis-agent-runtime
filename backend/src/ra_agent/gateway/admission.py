"""One local admission/commit order shared by workers and authority updates.

The OS releases the lock on process exit. No SQLite transaction is held across
awaits. A policy update acknowledges only after previously admitted work settles;
after acknowledgement, queued executions must check the new authority.
"""

from __future__ import annotations

import asyncio
import errno
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from ra_agent.database.workbench_store import SecurityProfileStore


class AdmissionGuard:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._owner: ContextVar[asyncio.Task[Any] | None] = ContextVar(
            "core_admission_owner", default=None
        )

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[None]:
        owner = asyncio.current_task()
        if self._owner.get() is owner and owner is not None:
            yield
            return
        with self.path.open("a+b") as stream:
            if os.fstat(stream.fileno()).st_size == 0:
                stream.write(b"0")
                stream.flush()
            while True:
                try:
                    stream.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    await asyncio.sleep(0.01)
            token = self._owner.set(owner)
            try:
                yield
            finally:
                self._owner.reset(token)
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class GuardedSecurityProfileStore:
    def __init__(self, store: SecurityProfileStore, guard: AdmissionGuard) -> None:
        self.store = store
        self.guard = guard

    async def ensure_default(self) -> dict[str, Any]:
        async with self.guard():
            return await self.store.ensure_default()

    async def get(self, profile_id: str, version: int | None = None) -> dict[str, Any] | None:
        return await self.store.get(profile_id, version)

    async def create_version(self, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self.guard():
            return await self.store.create_version(profile_id, payload)
