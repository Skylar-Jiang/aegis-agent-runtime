"""Run a complete synchronous operation off-loop, retaining cancellation ownership."""

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


def offload(function: Callable[P, T]) -> Callable[P, Awaitable[T]]:
    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        # Import lazily: contracts are also imported by the execution package.
        from ra_agent.execution._cancellation import complete_before_cancelling

        return await complete_before_cancelling(asyncio.to_thread(function, *args, **kwargs))

    return wrapped
