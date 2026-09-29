"""Indexed Core events with atomic per-task sequences and the v1 SM3 chain.

The database is trusted local storage, not an external cryptographic anchor.
Appending and reading a head validate the tail and its immediate predecessor;
list_task_events replays the complete requested task. Audit export then compares
that full history with its independently stored signed checkpoint. A rewritten
or truncated whole database cannot be detected without such an external anchor.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

from ra_agent.audit.integrity import CHAIN_DOMAIN, GENESIS_HASH, HashChain
from ra_agent.crypto import Canonicalizer, CryptoError, DigestProvider, load_json
from ra_agent.crypto.canonical import CRYPTO_UNAVAILABLE_CODES
from ra_agent.execution._cancellation import complete_before_cancelling

from .models import BehaviorEvent
from .store import EventStoreError

MAX_LEGACY_RECORD_BYTES = 4 * 1024 * 1024


class SqliteEventStore:
    """Keep the public EventStore API; use standard Core v1 crypto exclusively.

    Legacy JSONL is verified and imported once, in the schema transaction. The
    source is never removed or modified. Unchained/custom-chain legacy logs need
    an explicit migration outside this store; they are never relabelled as SM3.
    """

    def __init__(self, path: Path, *, legacy_path: Path | None = None) -> None:
        self.path = Path(path).resolve()
        self.legacy_path = Path(legacy_path).resolve() if legacy_path is not None else None
        if self.legacy_path == self.path:
            raise EventStoreError("MIGRATION_CONFLICT", "Database and legacy source must differ")
        self._canonical = Canonicalizer()
        self._digests = DigestProvider()
        self._initialize()

    async def append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        return await complete_before_cancelling(asyncio.to_thread(self._append, deepcopy(event)))

    async def list_task_events(self, task_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._list, task_id)

    async def list_task_event_page(
        self, task_id: str, *, after_sequence: int = 0, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Indexed UI page with local link checks; export still verifies full history."""
        if not 1 <= limit <= 1000 or min(after_sequence, offset) < 0:
            raise ValueError("invalid event page bounds")
        return await asyncio.to_thread(self._page, task_id, after_sequence, limit, offset)

    def _page(self, task_id: str, after: int, limit: int, offset: int) -> list[dict[str, Any]]:
        self._validate_task_id(task_id)
        with self._connection() as db:
            self._checked_tail(db, task_id)
            rows = db.execute(
                "SELECT * FROM core_events WHERE task_id=? AND sequence>? "
                "ORDER BY sequence LIMIT ? OFFSET ?",
                (task_id, after, limit, offset),
            ).fetchall()
            if not rows:
                return []
            previous = GENESIS_HASH
            sequence = rows[0]["sequence"] - 1
            if sequence:
                parent = db.execute(
                    "SELECT * FROM core_events WHERE task_id=? AND sequence=?", (task_id, sequence)
                ).fetchone()
                if parent is None:
                    raise EventStoreError("LOG_INVALID", "Preceding page event missing")
                previous = parent["chain_hash"]
            result = []
            for row in rows:
                item = self._decode_row(row)
                calculated = self._entry(item, previous)
                if (
                    item["sequence"] != sequence + 1
                    or any(
                        calculated[key] != row[key]
                        for key in ("prev_hash", "event_digest", "chain_hash")
                    )
                ):
                    raise EventStoreError("LOG_INVALID", "Event page chain is inconsistent")
                parent_id = item["parent_event_id"]
                if parent_id is not None and db.execute(
                    "SELECT 1 FROM core_events WHERE task_id=? AND event_id=? AND sequence<?",
                    (task_id, parent_id, item["sequence"]),
                ).fetchone() is None:
                    raise EventStoreError("LOG_INVALID", "Event page parent is missing or later")
                previous, sequence = row["chain_hash"], item["sequence"]
                result.append(item)
            return result

    async def get_chain_head(self, task_id: str) -> str | None:
        return await asyncio.to_thread(self._head, task_id)

    async def get_task_snapshot(self, task_id: str) -> tuple[list[dict[str, Any]], str]:
        """Return verified events and head from one SQLite read transaction."""
        return await asyncio.to_thread(self._snapshot, task_id)

    async def get_last_event_id(self, task_id: str) -> str | None:
        """Optional indexed parent lookup; the three-method EventStore API is unchanged."""
        return await asyncio.to_thread(self._last_event_id, task_id)

    @contextmanager
    def _connection(
        self, *, write: bool = False, initialize: bool = False
    ) -> Iterator[sqlite3.Connection]:
        db: sqlite3.Connection | None = None
        try:
            if initialize:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            db.row_factory = sqlite3.Row
            if initialize:
                db.execute("PRAGMA journal_mode = WAL")
            db.execute("PRAGMA synchronous = FULL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.execute("COMMIT")
        except EventStoreError:
            raise
        except CryptoError as exc:
            code = "CHECK_UNAVAILABLE" if exc.code in CRYPTO_UNAVAILABLE_CODES else "LOG_INVALID"
            raise EventStoreError(code, "Core event cryptographic check failed") from exc
        except (OSError, sqlite3.OperationalError) as exc:
            raise EventStoreError("STORAGE_UNAVAILABLE", "Core event storage unavailable") from exc
        except (sqlite3.DatabaseError, ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise EventStoreError("LOG_INVALID", "Core event storage validation failed") from exc
        finally:
            if db is not None:
                try:
                    if db.in_transaction:
                        db.rollback()
                finally:
                    db.close()

    def _initialize(self) -> None:
        with self._connection(write=True, initialize=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise EventStoreError("LOG_INVALID", "Unsupported Core event database version")
            db.execute(
                "CREATE TABLE IF NOT EXISTS core_events ("
                "task_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence > 0), "
                "event_id TEXT NOT NULL, parent_event_id TEXT, event_json BLOB NOT NULL, "
                "prev_hash TEXT NOT NULL, event_digest TEXT NOT NULL, chain_hash TEXT NOT NULL, "
                "PRIMARY KEY(task_id, sequence), UNIQUE(task_id, event_id))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS task_heads ("
                "task_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL, chain_hash TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS event_metadata "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            if self.legacy_path is not None:
                marker = db.execute(
                    "SELECT value FROM event_metadata WHERE key = 'legacy_import'"
                ).fetchone()
                if marker is not None:
                    if marker[0] != str(self.legacy_path):
                        raise EventStoreError("MIGRATION_CONFLICT", "Legacy source path changed")
                else:
                    self._import_legacy(db)
                    db.execute(
                        "INSERT INTO event_metadata(key, value) VALUES ('legacy_import', ?)",
                        (str(self.legacy_path),),
                    )
            db.execute("PRAGMA user_version = 1")

    def _import_legacy(self, db: sqlite3.Connection) -> None:
        assert self.legacy_path is not None
        try:
            stream = self.legacy_path.open("rb")
        except FileNotFoundError:
            return
        with stream:
            if db.execute("SELECT 1 FROM core_events LIMIT 1").fetchone() is not None:
                raise EventStoreError(
                    "MIGRATION_CONFLICT", "Cannot merge legacy and existing history"
                )
            while line := stream.readline(MAX_LEGACY_RECORD_BYTES + 1):
                if len(line) > MAX_LEGACY_RECORD_BYTES or not line.endswith(b"\n"):
                    raise EventStoreError("LOG_INVALID", "Oversized or incomplete legacy record")
                record = load_json(line)
                if (
                    not isinstance(record, dict)
                    or set(record) != {"storage_version", "event", "chain"}
                    or type(record["storage_version"]) is not int
                    or record["storage_version"] != 1
                    or not isinstance(record["chain"], dict)
                ):
                    raise EventStoreError("LOG_INVALID", "Unsupported or unchained legacy record")
                normalized = BehaviorEvent.model_validate(record["event"]).model_dump(mode="json")
                if normalized != record["event"]:
                    raise EventStoreError("LOG_INVALID", "Legacy event is not normalized")
                self._insert(db, normalized, legacy_chain=record["chain"])

    @staticmethod
    def _validate_task_id(task_id: Any) -> str:
        if not isinstance(task_id, str) or not 1 <= len(task_id) <= 128 or not task_id.strip():
            raise EventStoreError("EVENT_INVALID", "Invalid task_id")
        return task_id

    def _entry(self, event: dict[str, Any], previous: str) -> dict[str, Any]:
        if len(previous) != 64 or any(c not in "0123456789abcdef" for c in previous):
            raise EventStoreError("LOG_INVALID", "Invalid previous chain hash")
        digest = self._digests.sm3(self._canonical.canonicalize(event))
        head = self._digests.sm3(CHAIN_DOMAIN + bytes.fromhex(previous) + bytes.fromhex(digest))
        return {"event": event, "prev_hash": previous, "event_digest": digest, "chain_hash": head}

    def _decode_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = load_json(row["event_json"])
        event = BehaviorEvent.model_validate(payload).model_dump(mode="json")
        if event != payload or any(
            event[field] != row[field]
            for field in ("task_id", "sequence", "event_id", "parent_event_id")
        ):
            raise EventStoreError("LOG_INVALID", "Event and database index disagree")
        return event

    def _checked_tail(self, db: sqlite3.Connection, task_id: str) -> sqlite3.Row | None:
        head = db.execute("SELECT * FROM task_heads WHERE task_id = ?", (task_id,)).fetchone()
        tail = db.execute(
            "SELECT * FROM core_events WHERE task_id = ? ORDER BY sequence DESC LIMIT 1", (task_id,)
        ).fetchone()
        if head is None and tail is None:
            return None
        if head is None or tail is None:
            raise EventStoreError("LOG_INVALID", "Task head and event tail disagree")
        event = self._decode_row(tail)
        entry = self._entry(event, tail["prev_hash"])
        if (
            head["sequence"] != tail["sequence"]
            or head["chain_hash"] != tail["chain_hash"]
            or any(entry[field] != tail[field] for field in ("event_digest", "chain_hash"))
        ):
            raise EventStoreError("LOG_INVALID", "Task head or tail hash invalid")
        previous = GENESIS_HASH
        if tail["sequence"] > 1:
            row = db.execute(
                "SELECT chain_hash FROM core_events WHERE task_id = ? AND sequence = ?",
                (task_id, tail["sequence"] - 1),
            ).fetchone()
            if row is None:
                raise EventStoreError("LOG_INVALID", "Preceding event missing")
            previous = row["chain_hash"]
        if tail["prev_hash"] != previous:
            raise EventStoreError("LOG_INVALID", "Tail chain link invalid")
        return tail

    def _insert(
        self,
        db: sqlite3.Connection,
        event: dict[str, Any],
        *,
        legacy_chain: dict | None = None,
    ) -> dict[str, Any]:
        if not isinstance(event, dict):
            raise EventStoreError("EVENT_INVALID", "Event must be a JSON object")
        task_id = self._validate_task_id(event.get("task_id"))
        tail = self._checked_tail(db, task_id)
        sequence = tail["sequence"] + 1 if tail is not None else 1
        event.setdefault("sequence", sequence)
        try:
            normalized = BehaviorEvent.model_validate(event).model_dump(mode="json")
        except ValueError as exc:
            raise EventStoreError("EVENT_INVALID", "Invalid Core event") from exc
        if (
            db.execute(
                "SELECT 1 FROM core_events WHERE task_id = ? AND event_id = ?",
                (task_id, normalized["event_id"]),
            ).fetchone()
            is not None
        ):
            raise EventStoreError("EVENT_ID_CONFLICT", "event_id already exists in this task")
        if normalized["sequence"] != sequence:
            raise EventStoreError("SEQUENCE_INVALID", "sequence must be the next task sequence")
        parent = normalized["parent_event_id"]
        if (
            parent is not None
            and db.execute(
                "SELECT 1 FROM core_events WHERE task_id = ? AND event_id = ?", (task_id, parent)
            ).fetchone()
            is None
        ):
            raise EventStoreError("PARENT_EVENT_MISSING", "parent must precede event in this task")
        entry = self._entry(normalized, tail["chain_hash"] if tail is not None else GENESIS_HASH)
        if legacy_chain is not None and legacy_chain != entry:
            raise EventStoreError("LOG_INVALID", "Legacy chain differs from Core v1 reconstruction")
        db.execute(
            "INSERT INTO core_events(task_id, sequence, event_id, parent_event_id, "
            "event_json, prev_hash, event_digest, chain_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                sequence,
                normalized["event_id"],
                parent,
                self._canonical.canonicalize(normalized),
                entry["prev_hash"],
                entry["event_digest"],
                entry["chain_hash"],
            ),
        )
        db.execute(
            "INSERT INTO task_heads(task_id, sequence, chain_hash) VALUES (?, ?, ?) "
            "ON CONFLICT(task_id) DO UPDATE SET sequence=excluded.sequence, "
            "chain_hash=excluded.chain_hash",
            (task_id, sequence, entry["chain_hash"]),
        )
        return normalized

    def _append(self, event: dict[str, Any]) -> dict[str, Any]:
        with self._connection(write=True) as db:
            return self._insert(db, event)

    def _snapshot(self, task_id: str) -> tuple[list[dict[str, Any]], str]:
        self._validate_task_id(task_id)
        with self._connection() as db:
            tail = self._checked_tail(db, task_id)
            result = self._replay_task(db, task_id, tail)
            return result, tail["chain_hash"] if tail is not None else GENESIS_HASH

    def _list(self, task_id: str) -> list[dict[str, Any]]:
        return self._snapshot(task_id)[0]

    def _replay_task(
        self, db: sqlite3.Connection, task_id: str, tail: sqlite3.Row | None
    ) -> list[dict[str, Any]]:
        chain = HashChain(task_id)
        result: list[dict[str, Any]] = []
        for row in db.execute(
            "SELECT * FROM core_events WHERE task_id = ? ORDER BY sequence", (task_id,)
        ):
            event = self._decode_row(row)
            entry = chain.append_event(event)
            if any(
                entry[field] != row[field]
                for field in ("prev_hash", "event_digest", "chain_hash")
            ):
                raise EventStoreError("LOG_INVALID", "Stored chain differs from task replay")
            result.append(event)
        if tail is not None and chain.get_chain_head() != tail["chain_hash"]:
            raise EventStoreError("LOG_INVALID", "Task replay differs from head")
        return result

    def _head(self, task_id: str) -> str:
        self._validate_task_id(task_id)
        with self._connection() as db:
            tail = self._checked_tail(db, task_id)
            return tail["chain_hash"] if tail is not None else GENESIS_HASH

    def _last_event_id(self, task_id: str) -> str | None:
        self._validate_task_id(task_id)
        with self._connection() as db:
            tail = self._checked_tail(db, task_id)
            return tail["event_id"] if tail is not None else None
