"""Durable SecurityProfile versions and Conversation messages."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from sqlalchemy import func, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from .models import ConversationMessageRow, ConversationRow, SecurityProfileVersionRow

DEFAULT_PROFILE: dict[str, Any] = {
    "allowed_actions": [
        "list_dir",
        "read_file",
        "create_file",
        "write_file",
        "memory_read",
        "memory_write",
    ],
    "resource_scopes": ["**"],
    "allow_egress": False,
    "max_affected_objects": 100,
    "approval_policy": {
        "required_actions": ["delete_file", "run_shell", "send_email_dry_run"],
        "bulk_action_threshold": 20,
    },
}


class SecurityProfileStore(Protocol):
    async def ensure_default(self) -> dict[str, Any]: ...
    async def get(self, profile_id: str, version: int | None = None) -> dict[str, Any] | None: ...
    async def create_version(self, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class ConversationStore(Protocol):
    async def create(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    async def get(self, conversation_id: str) -> dict[str, Any] | None: ...
    async def list(self, *, limit: int = 50) -> list[dict[str, Any]]: ...
    async def add_message(self, payload: dict[str, Any]) -> dict[str, Any]: ...
    async def list_messages(self, conversation_id: str) -> list[dict[str, Any]]: ...
    async def update_summary(self, conversation_id: str, summary: str) -> None: ...
    async def set_message_task(self, message_id: str, task_id: str) -> None: ...
    async def update_title(self, conversation_id: str, title: str) -> None: ...


def _profile_payload(profile_id: str, version: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile_id": profile_id,
        "version": version,
        "allowed_actions": list(payload["allowed_actions"]),
        "resource_scopes": list(payload["resource_scopes"]),
        "allow_egress": bool(payload.get("allow_egress", False)),
        "max_affected_objects": int(payload.get("max_affected_objects", 100)),
        "approval_policy": deepcopy(payload.get("approval_policy", {})),
        "created_at": datetime.now(UTC).isoformat(),
    }


class InMemorySecurityProfileStore:
    def __init__(self) -> None:
        self._profiles: dict[str, list[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def ensure_default(self) -> dict[str, Any]:
        async with self._lock:
            versions = self._profiles.setdefault("default", [])
            if not versions:
                versions.append(_profile_payload("default", 1, DEFAULT_PROFILE))
            return deepcopy(versions[-1])

    async def get(self, profile_id: str, version: int | None = None) -> dict[str, Any] | None:
        async with self._lock:
            versions = self._profiles.get(profile_id, [])
            if not versions:
                return None
            if version is None:
                return deepcopy(versions[-1])
            found = next((item for item in versions if item["version"] == version), None)
            return deepcopy(found) if found is not None else None

    async def create_version(self, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            versions = self._profiles.setdefault(profile_id, [])
            item = _profile_payload(profile_id, len(versions) + 1, payload)
            versions.append(item)
            return deepcopy(item)


class PersistentSecurityProfileStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine, expire_on_commit=False
        )

    async def ensure_default(self) -> dict[str, Any]:
        async with self._sessions() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            existing = await session.scalar(
                select(SecurityProfileVersionRow)
                .where(SecurityProfileVersionRow.profile_id == "default")
                .order_by(SecurityProfileVersionRow.version.desc())
                .limit(1)
            )
            if existing is not None:
                return self._view(existing)
            item = await self._add_version(session, "default", DEFAULT_PROFILE)
            await session.commit()
            return item

    async def get(self, profile_id: str, version: int | None = None) -> dict[str, Any] | None:
        async with self._sessions() as session:
            statement = select(SecurityProfileVersionRow).where(
                SecurityProfileVersionRow.profile_id == profile_id
            )
            if version is None:
                statement = statement.order_by(SecurityProfileVersionRow.version.desc()).limit(1)
            else:
                statement = statement.where(SecurityProfileVersionRow.version == version)
            row = await session.scalar(statement)
            return self._view(row) if row is not None else None

    async def create_version(self, profile_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._sessions() as session:
            # Reserve the database writer before reading MAX; every process using
            # this SQLite database participates, not only this store instance.
            await session.execute(text("BEGIN IMMEDIATE"))
            item = await self._add_version(session, profile_id, payload)
            await session.commit()
            return item

    @staticmethod
    async def _add_version(
        session: AsyncSession, profile_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        current = await session.scalar(
            select(func.max(SecurityProfileVersionRow.version)).where(
                SecurityProfileVersionRow.profile_id == profile_id
            )
        )
        item = _profile_payload(profile_id, int(current or 0) + 1, payload)
        session.add(
            SecurityProfileVersionRow(**{**item, "allow_egress": int(item["allow_egress"])})
        )
        return item

    @staticmethod
    def _view(row: SecurityProfileVersionRow) -> dict[str, Any]:
        return {
            "profile_id": row.profile_id,
            "version": row.version,
            "allowed_actions": list(row.allowed_actions),
            "resource_scopes": list(row.resource_scopes),
            "allow_egress": bool(row.allow_egress),
            "max_affected_objects": row.max_affected_objects,
            "approval_policy": deepcopy(row.approval_policy),
            "created_at": row.created_at,
        }


class InMemoryConversationStore:
    def __init__(self) -> None:
        self._conversations: dict[str, dict[str, Any]] = {}
        self._messages: dict[str, list[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            self._conversations[payload["conversation_id"]] = deepcopy(payload)
            self._messages[payload["conversation_id"]] = []
            return deepcopy(payload)

    async def get(self, conversation_id: str) -> dict[str, Any] | None:
        async with self._lock:
            item = self._conversations.get(conversation_id)
            return deepcopy(item) if item is not None else None

    async def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        async with self._lock:
            items = sorted(
                self._conversations.values(),
                key=lambda item: str(item["updated_at"]),
                reverse=True,
            )
            return deepcopy(items[:limit])

    async def add_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            messages = self._messages.get(payload["conversation_id"])
            if messages is None:
                raise KeyError(payload["conversation_id"])
            item = {**payload, "sequence_number": len(messages) + 1}
            messages.append(deepcopy(item))
            self._conversations[payload["conversation_id"]]["updated_at"] = payload["created_at"]
            return deepcopy(item)

    async def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            if conversation_id not in self._messages:
                raise KeyError(conversation_id)
            return deepcopy(self._messages[conversation_id])

    async def update_summary(self, conversation_id: str, summary: str) -> None:
        async with self._lock:
            if conversation_id not in self._conversations:
                raise KeyError(conversation_id)
            self._conversations[conversation_id]["context_summary"] = summary
            self._conversations[conversation_id]["updated_at"] = datetime.now(UTC).isoformat()

    async def set_message_task(self, message_id: str, task_id: str) -> None:
        async with self._lock:
            for messages in self._messages.values():
                for item in messages:
                    if item["message_id"] == message_id:
                        item["task_id"] = task_id
                        return
            raise KeyError(message_id)

    async def update_title(self, conversation_id: str, title: str) -> None:
        async with self._lock:
            if conversation_id not in self._conversations:
                raise KeyError(conversation_id)
            self._conversations[conversation_id]["title"] = title
            self._conversations[conversation_id]["updated_at"] = datetime.now(UTC).isoformat()


class PersistentConversationStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(
            engine, expire_on_commit=False
        )

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._sessions() as session:
            session.add(ConversationRow(**payload))
            await session.commit()
        return deepcopy(payload)

    async def get(self, conversation_id: str) -> dict[str, Any] | None:
        async with self._sessions() as session:
            row = await session.get(ConversationRow, conversation_id)
            return self._conversation_view(row) if row is not None else None

    async def list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(ConversationRow).order_by(ConversationRow.updated_at.desc()).limit(limit)
                )
            ).all()
            return [self._conversation_view(row) for row in rows]

    async def add_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._sessions() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            conversation = await session.get(ConversationRow, payload["conversation_id"])
            if conversation is None:
                raise KeyError(payload["conversation_id"])
            current = await session.scalar(
                select(func.max(ConversationMessageRow.sequence_number)).where(
                    ConversationMessageRow.conversation_id == payload["conversation_id"]
                )
            )
            item = {**payload, "sequence_number": int(current or 0) + 1}
            session.add(ConversationMessageRow(**item))
            conversation.updated_at = payload["created_at"]
            await session.commit()
            return item

    async def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            if await session.get(ConversationRow, conversation_id) is None:
                raise KeyError(conversation_id)
            rows = (
                await session.scalars(
                    select(ConversationMessageRow)
                    .where(ConversationMessageRow.conversation_id == conversation_id)
                    .order_by(ConversationMessageRow.sequence_number)
                )
            ).all()
            return [self._message_view(row) for row in rows]

    async def update_summary(self, conversation_id: str, summary: str) -> None:
        async with self._sessions() as session:
            conversation = await session.get(ConversationRow, conversation_id)
            if conversation is None:
                raise KeyError(conversation_id)
            conversation.context_summary = summary
            conversation.updated_at = datetime.now(UTC).isoformat()
            await session.commit()

    async def set_message_task(self, message_id: str, task_id: str) -> None:
        async with self._sessions() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(
                    update(ConversationMessageRow)
                    .where(ConversationMessageRow.message_id == message_id)
                    .values(task_id=task_id)
                ),
            )
            if not result.rowcount:
                await session.rollback()
                raise KeyError(message_id)
            await session.commit()

    async def update_title(self, conversation_id: str, title: str) -> None:
        async with self._sessions() as session:
            conversation = await session.get(ConversationRow, conversation_id)
            if conversation is None:
                raise KeyError(conversation_id)
            conversation.title = title
            conversation.updated_at = datetime.now(UTC).isoformat()
            await session.commit()

    @staticmethod
    def _conversation_view(row: ConversationRow) -> dict[str, Any]:
        return {
            "conversation_id": row.conversation_id,
            "title": row.title,
            "security_profile_id": row.security_profile_id,
            "context_summary": row.context_summary,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _message_view(row: ConversationMessageRow) -> dict[str, Any]:
        return {
            "message_id": row.message_id,
            "conversation_id": row.conversation_id,
            "role": row.role,
            "content": row.content,
            "task_id": row.task_id,
            "sequence_number": row.sequence_number,
            "created_at": row.created_at,
        }
