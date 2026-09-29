import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from ra_agent.database.models import Base
from ra_agent.database.workbench_store import (
    DEFAULT_PROFILE,
    PersistentConversationStore,
    PersistentSecurityProfileStore,
)


@pytest.fixture
async def engines(tmp_path: Path) -> AsyncIterator[list[AsyncEngine]]:
    url = f"sqlite+aiosqlite:///{(tmp_path / 'workbench.db').as_posix()}"
    instances = [create_async_engine(url) for _ in range(3)]
    try:
        async with instances[0].begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield instances
    finally:
        for engine in instances:
            await engine.dispose()


@pytest.mark.asyncio
async def test_independent_profile_stores_allocate_every_concurrent_version(engines):
    stores = [PersistentSecurityProfileStore(engine) for engine in engines]
    await stores[0].create_version("profile", DEFAULT_PROFILE)
    payloads = [
        deepcopy(DEFAULT_PROFILE) | {"max_affected_objects": i} for i in range(1, 25)
    ]
    results = await asyncio.gather(
        *(
            stores[i % 3].create_version("profile", payload)
            for i, payload in enumerate(payloads)
        ),
        return_exceptions=True,
    )
    assert not [result for result in results if isinstance(result, BaseException)]
    assert sorted(result["version"] for result in results if isinstance(result, dict)) == list(
        range(2, 26)
    )
    restored = PersistentSecurityProfileStore(engines[2])
    assert {
        ((await restored.get("profile", version)) or {})["max_affected_objects"]
        for version in range(2, 26)
    } == set(range(1, 25))
    original = await restored.get("profile", 1)
    assert original is not None and original["max_affected_objects"] == 100


@pytest.mark.asyncio
async def test_concurrent_default_initialization_creates_only_one_version(engines):
    stores = [PersistentSecurityProfileStore(engine) for engine in engines]
    results = await asyncio.gather(
        *(stores[i % 3].ensure_default() for i in range(18)), return_exceptions=True
    )
    assert not [result for result in results if isinstance(result, BaseException)]
    assert {result["version"] for result in results if isinstance(result, dict)} == {1}
    assert await stores[0].get("default", 2) is None


@pytest.mark.asyncio
async def test_independent_conversation_stores_preserve_all_messages_and_sequence(
    engines,
):
    stores = [PersistentConversationStore(engine) for engine in engines]
    await stores[0].create(
        {
            "conversation_id": "conversation",
            "title": "Concurrent messages",
            "security_profile_id": "default",
            "context_summary": "",
            "created_at": "2026-09-23T00:00:00Z",
            "updated_at": "2026-09-23T00:00:00Z",
        }
    )
    payloads = [
        {
            "message_id": f"message-{i}",
            "conversation_id": "conversation",
            "role": "user",
            "content": f"independent message {i}",
            "task_id": None,
            "created_at": "2026-09-23T00:00:00Z",
        }
        for i in range(24)
    ]
    results = await asyncio.gather(
        *(stores[i % 3].add_message(payload) for i, payload in enumerate(payloads)),
        return_exceptions=True,
    )
    assert not [result for result in results if isinstance(result, BaseException)]
    restored = await PersistentConversationStore(engines[2]).list_messages(
        "conversation"
    )
    assert [message["sequence_number"] for message in restored] == list(range(1, 25))
    assert {message["message_id"] for message in restored} == {
        p["message_id"] for p in payloads
    }
    assert {message["content"] for message in restored} == {
        p["content"] for p in payloads
    }
    with pytest.raises(IntegrityError):
        await stores[0].add_message(payloads[0])
    appended = await stores[1].add_message(payloads[0] | {"message_id": "next"})
    assert appended["sequence_number"] == 25


@pytest.mark.asyncio
async def test_separate_processes_allocate_profile_versions_and_message_sequences(
    engines,
):
    conversations = PersistentConversationStore(engines[0])
    await conversations.create(
        {
            "conversation_id": "conversation",
            "title": "Concurrent processes",
            "security_profile_id": "default",
            "context_summary": "",
            "created_at": "2026-09-23T00:00:00Z",
            "updated_at": "2026-09-23T00:00:00Z",
        }
    )
    script = """
import asyncio, sys
from sqlalchemy.ext.asyncio import create_async_engine
from ra_agent.database.workbench_store import (
    DEFAULT_PROFILE, PersistentConversationStore, PersistentSecurityProfileStore,
)
async def main():
    engine = create_async_engine(sys.argv[1])
    try:
        profiles = PersistentSecurityProfileStore(engine)
        messages = PersistentConversationStore(engine)
        await profiles.ensure_default()
        for i in range(8):
            await profiles.create_version('profile', DEFAULT_PROFILE)
            await messages.add_message({
                'message_id': f'{sys.argv[2]}-{i}', 'conversation_id': 'conversation',
                'role': 'user', 'content': f'process {sys.argv[2]} message {i}',
                'task_id': None, 'created_at': '2026-09-23T00:00:00Z',
            })
    finally:
        await engine.dispose()
asyncio.run(main())
"""

    def run(worker):
        return subprocess.run(
            [sys.executable, "-c", script, str(engines[0].url), str(worker)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=False,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(__file__).parents[3] / "backend/src"),
            },
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    results = await asyncio.gather(
        *(asyncio.to_thread(run, worker) for worker in range(2))
    )
    assert all(result.returncode == 0 for result in results), [
        r.stderr for r in results
    ]
    messages = await conversations.list_messages("conversation")
    assert [message["sequence_number"] for message in messages] == list(range(1, 17))
    assert {message["message_id"] for message in messages} == {
        f"{worker}-{i}" for worker in range(2) for i in range(8)
    }
    profiles = PersistentSecurityProfileStore(engines[1])
    profile = await profiles.get("profile")
    default = await profiles.get("default")
    assert profile is not None and profile["version"] == 16
    assert default is not None and default["version"] == 1
