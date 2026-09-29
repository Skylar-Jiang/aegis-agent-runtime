from pathlib import Path

import pytest
from ra_agent.database.models import Base
from ra_agent.database.task_store import PersistentTaskStore
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_task_snapshot_survives_store_recreation(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    first = PersistentTaskStore(engine)
    await first.create(
        {
            "task_id": "task-persisted",
            "objective": "read README.md",
            "status": "WAITING_APPROVAL",
            "created_at": "2026-09-11T00:00:00+00:00",
            "updated_at": "2026-09-11T00:00:00+00:00",
            "final_answer": None,
            "contract": {"allowed_actions": ["read_file"]},
            "agent_state": {"status": "WAITING_APPROVAL"},
        }
    )

    second = PersistentTaskStore(engine)
    restored = await second.get("task-persisted")

    assert restored is not None
    assert restored["status"] == "WAITING_APPROVAL"
    assert restored["agent_state"] == {"status": "WAITING_APPROVAL"}
    await engine.dispose()


@pytest.mark.asyncio
async def test_running_task_is_marked_interrupted_after_restart(tmp_path: Path) -> None:
    database = tmp_path / "runtime.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database.as_posix()}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    store = PersistentTaskStore(engine)
    await store.create(
        {
            "task_id": "task-running",
            "objective": "long task",
            "status": "RUNNING",
            "created_at": "2026-09-11T00:00:00+00:00",
            "updated_at": "2026-09-11T00:00:00+00:00",
            "final_answer": None,
            "contract": None,
            "agent_state": None,
        }
    )

    changed = await store.mark_orphaned_running_tasks()
    restored = await store.get("task-running")

    assert changed == 1
    assert restored is not None
    assert restored["status"] == "INTERRUPTED"
    await engine.dispose()
