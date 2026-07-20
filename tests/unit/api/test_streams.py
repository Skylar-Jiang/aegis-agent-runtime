from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import cast

import pytest

from ra_agent.api.streams import _event_generator
from ra_agent.core.container import ServiceContainer


def _event(sequence: int) -> dict[str, object]:
    return {
        "event_id": f"event-{sequence}",
        "task_id": "task-1",
        "sequence_number": sequence,
        "event_type": "TOOL_REQUESTED",
        "timestamp": "2026-07-20T00:00:00+00:00",
        "actor": "runtime",
        "status": "RUNNING",
        "summary": f"event {sequence}",
    }


class ReplayRecorder:
    def __init__(self) -> None:
        self.events = [_event(1)]
        self.queue: asyncio.Queue[dict[str, object]] | None = None

    async def subscribe(self, task_id: str) -> asyncio.Queue[dict[str, object]]:
        self.queue = asyncio.Queue()
        arrived_during_replay = _event(2)
        self.events.append(arrived_during_replay)
        await self.queue.put(arrived_during_replay)
        return self.queue

    async def unsubscribe(self, task_id: str, queue: asyncio.Queue[dict[str, object]]) -> None:
        return None

    async def events_for(
        self, task_id: str, *, limit: int, offset: int
    ) -> list[dict[str, object]]:
        return self.events


@pytest.mark.asyncio
async def test_stream_replays_and_deduplicates_events_arriving_during_replay() -> None:
    recorder = ReplayRecorder()
    services = cast(ServiceContainer, SimpleNamespace(audit_recorder=recorder))
    generator = cast(AsyncGenerator[str, None], _event_generator("task-1", services))

    first = await anext(generator)
    second = await anext(generator)

    assert first.startswith("id: 1\n")
    assert second.startswith("id: 2\n")

    await recorder.queue.put(_event(3))  # type: ignore[union-attr]
    third = await anext(generator)
    assert third.startswith("id: 3\n")
    await generator.aclose()
