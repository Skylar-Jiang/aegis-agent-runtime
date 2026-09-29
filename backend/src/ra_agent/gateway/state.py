"""Short, atomic Core state transactions, in memory or in a local SQLite file.

No SQLite connection or transaction survives an async suspension. Disk stores use
BEGIN IMMEDIATE so independent processes share the request execution claim.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock
from typing import Any, TypeVar

from ra_agent.core.offload import offload

T = TypeVar("T")


class CoreStateTransaction:
    def __init__(
        self,
        *,
        connection: sqlite3.Connection | None = None,
        memory: dict[tuple[str, str], Any] | None = None,
    ) -> None:
        self._connection = connection
        self._memory = memory if memory is not None else {}

    def get(self, namespace: str, key: str) -> Any:
        if self._connection is None:
            return deepcopy(self._memory.get((namespace, key)))
        row = self._connection.execute(
            "SELECT payload FROM core_state WHERE namespace = ? AND key = ?", (namespace, key)
        ).fetchone()
        return json.loads(row[0]) if row is not None else None

    def put(self, namespace: str, key: str, value: Any) -> None:
        # Validate/clone even in memory: callers cannot retain aliases to stored state.
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if self._connection is None:
            self._memory[(namespace, key)] = json.loads(payload)
        else:
            self._connection.execute(
                "INSERT INTO core_state(namespace, key, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(namespace, key) DO UPDATE SET payload = excluded.payload",
                (namespace, key, payload),
            )

    def requests_for_task(self, task_id: str) -> Iterator[dict[str, Any]]:
        """Read one task's history when first seeding an upgraded quota counter."""
        if self._connection is None:
            for (namespace, _), value in self._memory.items():
                if namespace == "requests" and value["envelope"]["task_id"] == task_id:
                    yield deepcopy(value)
            return
        rows = self._connection.execute(
            "SELECT payload FROM core_state WHERE namespace='requests' "
            "AND json_extract(payload, '$.envelope.task_id')=?",
            (task_id,),
        )
        for row in rows:
            yield json.loads(row[0])

    def all_requests(self) -> Iterator[dict[str, Any]]:
        """One-time migration for a previously uncounted cross-task grant."""
        if self._connection is None:
            for (namespace, _), value in self._memory.items():
                if namespace == "requests":
                    yield deepcopy(value)
            return
        for row in self._connection.execute(
            "SELECT payload FROM core_state WHERE namespace='requests'"
        ):
            yield json.loads(row[0])


class CoreStateStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else None
        self._memory: dict[tuple[str, str], Any] = {}
        self._lock = RLock()
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.transaction() as transaction:
                assert transaction._connection is not None
                transaction._connection.execute(
                    "CREATE TABLE IF NOT EXISTS core_state ("
                    "namespace TEXT NOT NULL, key TEXT NOT NULL, payload TEXT NOT NULL, "
                    "PRIMARY KEY(namespace, key))"
                )
                transaction._connection.execute(
                    "CREATE INDEX IF NOT EXISTS core_request_task ON core_state "
                    "(json_extract(payload, '$.envelope.task_id')) WHERE namespace='requests'"
                )

    @contextmanager
    def transaction(self) -> Iterator[CoreStateTransaction]:
        with self._lock:
            if self.path is None:
                working = deepcopy(self._memory)
                yield CoreStateTransaction(memory=working)
                self._memory = working
                return
            connection = sqlite3.connect(self.path, timeout=30)
            try:
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("BEGIN IMMEDIATE")
                yield CoreStateTransaction(connection=connection)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self.read_transaction() as transaction:
            return transaction.get("sessions", session_id)

    @contextmanager
    def read_transaction(self) -> Iterator[CoreStateTransaction]:
        """A read snapshot does not acquire SQLite's writer reservation."""
        if self.path is None:
            with self._lock:
                snapshot = deepcopy(self._memory)
            yield CoreStateTransaction(memory=snapshot)
            return
        connection = sqlite3.connect(self.path, timeout=30)
        try:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("BEGIN")
            yield CoreStateTransaction(connection=connection)
        finally:
            connection.rollback()
            connection.close()

    @offload
    def run(self, operation: Callable[[CoreStateTransaction], T], *, readonly: bool = False) -> T:
        """The entire transaction lives in one worker; no connection crosses an await."""
        with self.read_transaction() if readonly else self.transaction() as transaction:
            return operation(transaction)

    async def get(self, namespace: str, key: str) -> Any:
        return await self.run(lambda tx: tx.get(namespace, key), readonly=True)

    def set_session(self, session_id: str, session: dict[str, Any]) -> None:
        with self.transaction() as transaction:
            transaction.put("sessions", session_id, session)
