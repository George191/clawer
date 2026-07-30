from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token

ControlCheckpoint = Callable[[], Awaitable[bool]]

_control_checkpoint: ContextVar[ControlCheckpoint | None] = ContextVar(
    "control_checkpoint", default=None
)


def set_control_checkpoint(checkpoint: ControlCheckpoint) -> Token[ControlCheckpoint | None]:
    return _control_checkpoint.set(checkpoint)


def reset_control_checkpoint(token: Token[ControlCheckpoint | None]) -> None:
    _control_checkpoint.reset(token)


async def check_control_state() -> None:
    checkpoint = _control_checkpoint.get()
    if checkpoint is not None and not await checkpoint():
        raise asyncio.CancelledError


async def controlled_sleep(seconds: float, poll_interval: float = 1.0) -> None:
    """Sleep while allowing the current task to pause or cancel cooperatively."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, seconds)
    while True:
        await check_control_state()
        remaining = deadline - loop.time()
        if remaining <= 0:
            return
        await asyncio.sleep(min(poll_interval, remaining))
