"""Wait for operations with irreversible worker activity before propagating cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import TypeVar

_Result = TypeVar("_Result")


async def complete_before_cancelling(
    operation: Awaitable[_Result],
    *,
    propagate_cancellation: bool = True,
) -> _Result:
    """Keep ownership until completion, including when the caller is cancelled repeatedly.

    In particular, cancelling ``to_thread`` does not stop its filesystem worker.
    Every wait stays shielded so a second cancellation cannot release ownership early.
    Cleanup callers can suppress cancellation because they already propagate the cause.
    """

    task = asyncio.ensure_future(operation)
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError as error:
            if task.cancelled():
                raise
            if cancellation is None:
                cancellation = error
        except Exception as error:
            if cancellation is None or not propagate_cancellation:
                raise
            cancellation.add_note(f"operation also failed: {type(error).__name__}: {error}")
            raise cancellation from error
    if cancellation is not None and propagate_cancellation:
        raise cancellation
    return result
