"""Small, single-process JSONL event store with atomic snapshot replacement.

Every operation rereads the snapshot under a per-path process lock. This supports
multiple instances/threads in one process, not independent writer processes.
No cryptographic implementation is supplied here; P3's HashChain can be injected.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from threading import Lock, RLock
from typing import Any, Protocol

from pydantic import ValidationError

from ra_agent.execution._cancellation import complete_before_cancelling

from .models import BehaviorEvent

MAX_LOG_BYTES = 16 * 1024 * 1024
_locks: dict[Path, RLock] = {}
_locks_guard = Lock()


class EventStoreError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class EventChain(Protocol):
    """Structural subset of P3 HashChain; this service is synchronous."""

    def append_event(self, event: dict[str, Any]) -> dict[str, Any]: ...

    def get_chain_head(self) -> str: ...


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _no_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON constant: {value}")


class JsonlEventStore:
    def __init__(
        self, path: Path, *, chain_factory: Callable[[str], EventChain] | None = None
    ) -> None:
        self.path = path.resolve()
        self._chain_factory = chain_factory
        with _locks_guard:
            self._lock = _locks.setdefault(self.path, RLock())

    async def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        # Snapshot before crossing the thread boundary; never mutate caller data.
        return await complete_before_cancelling(asyncio.to_thread(self._append, deepcopy(event)))

    async def list_task_events(self, task_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list, task_id)

    async def get_chain_head(self, task_id: str) -> str | None:
        return await asyncio.to_thread(self._head, task_id)

    async def get_task_snapshot(
        self, task_id: str
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Return events and chain head from one locked JSONL snapshot."""
        return await asyncio.to_thread(self._snapshot, task_id)

    def _load(self) -> tuple[list[dict[str, Any]], dict[str, EventChain]]:
        try:
            if not self.path.exists():
                return [], {}
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_LOG_BYTES + 1)
            if len(raw) > MAX_LOG_BYTES:
                raise ValueError("log exceeds the 16 MiB PR1 limit")
            if raw and not raw.endswith(b"\n"):
                raise ValueError("incomplete JSONL record")
            records: list[dict[str, Any]] = []
            ids: dict[str, set[str]] = {}
            chains: dict[str, EventChain] = {}
            for line in raw.decode("utf-8").splitlines():
                record = json.loads(
                    line, object_pairs_hook=_unique_object, parse_constant=_no_constant
                )
                if (
                    not isinstance(record, dict)
                    or set(record) != {"storage_version", "event", "chain"}
                    or type(record["storage_version"]) is not int
                    or record["storage_version"] != 1
                ):
                    raise ValueError("unsupported storage record")
                event = BehaviorEvent.model_validate(record["event"]).model_dump(mode="json")
                if event != record["event"]:
                    raise ValueError("stored event must retain its normalized representation")
                task_ids = ids.setdefault(event["task_id"], set())
                self._check_order(event, task_ids)
                self._check_chain(event, record["chain"], chains)
                task_ids.add(event["event_id"])
                records.append(record)
            return records, chains
        except OSError as error:
            raise EventStoreError("STORAGE_UNAVAILABLE", "Cannot read event log") from error
        except (ValueError, TypeError, KeyError, UnicodeError) as error:
            raise EventStoreError("LOG_INVALID", f"Event log validation failed: {error}") from error

    @staticmethod
    def _check_order(event: dict[str, Any], ids: set[str]) -> None:
        if event["event_id"] in ids:
            raise EventStoreError("EVENT_ID_CONFLICT", "event_id already exists in this task")
        if event["sequence"] != len(ids) + 1:
            raise EventStoreError("SEQUENCE_INVALID", "sequence must be the next task sequence")
        if event["parent_event_id"] is not None and event["parent_event_id"] not in ids:
            raise EventStoreError("PARENT_EVENT_MISSING", "parent must precede event in this task")

    def _check_chain(
        self, event: dict[str, Any], stored: object, chains: dict[str, EventChain]
    ) -> None:
        if self._chain_factory is None:
            if stored is not None:
                raise ValueError("chained log requires its chain provider")
            return
        task_id = event["task_id"]
        if task_id not in chains:
            chains[task_id] = self._chain_factory(task_id)
        actual = chains[task_id].append_event(deepcopy(event))
        if stored != actual:
            raise ValueError("stored chain differs from provider reconstruction")

    def _append(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            records, chains = self._load()
            if not isinstance(event, dict):
                raise EventStoreError("EVENT_INVALID", "event must be a JSON object")
            task_id = event.get("task_id")
            existing = [r["event"] for r in records if r["event"]["task_id"] == task_id]
            event.setdefault("sequence", len(existing) + 1)
            try:
                stored = BehaviorEvent.model_validate(event).model_dump(mode="json")
            except ValidationError as error:
                raise EventStoreError("EVENT_INVALID", str(error)) from error
            self._check_order(stored, {item["event_id"] for item in existing})
            chain = None
            if self._chain_factory is not None:
                if task_id not in chains:
                    chains[stored["task_id"]] = self._chain_factory(stored["task_id"])
                chain = chains[stored["task_id"]].append_event(deepcopy(stored))
            records.append({"storage_version": 1, "event": stored, "chain": chain})
            self._persist(records)
            return deepcopy(stored)

    def _persist(self, records: list[dict[str, Any]]) -> None:
        temporary: Path | None = None
        try:
            payload = "".join(
                json.dumps(row, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n"
                for row in records
            ).encode("utf-8")
            if len(payload) > MAX_LOG_BYTES:
                raise EventStoreError("LOG_TOO_LARGE", "log exceeds the 16 MiB PR1 limit")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError as error:
            raise EventStoreError("STORAGE_UNAVAILABLE", "Cannot persist event log") from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _list(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            records, _ = self._load()
            return [r["event"] for r in records if r["event"]["task_id"] == task_id]

    def _head(self, task_id: str) -> str | None:
        with self._lock:
            records, chains = self._load()
            if self._chain_factory is not None:
                if task_id in chains:
                    return chains[task_id].get_chain_head()
                return self._chain_factory(task_id).get_chain_head()
            if any(r["event"]["task_id"] == task_id for r in records):
                raise EventStoreError(
                    "CHECK_UNAVAILABLE", "No cryptographic chain provider configured"
                )
            return None

    def _snapshot(self, task_id: str) -> tuple[list[dict[str, Any]], str | None]:
        with self._lock:
            records, chains = self._load()
            events = [r["event"] for r in records if r["event"]["task_id"] == task_id]
            if self._chain_factory is None:
                if events:
                    raise EventStoreError(
                        "CHECK_UNAVAILABLE", "No cryptographic chain provider configured"
                    )
                return events, None
            chain = chains.get(task_id)
            if chain is None:
                chain = self._chain_factory(task_id)
            return events, chain.get_chain_head()
