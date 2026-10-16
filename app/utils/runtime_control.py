from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token

ControlCheckpoint = Callable[[], Awaitable[bool]]

_control_checkpoint: ContextVar[ControlCheckpoint | None] = ContextVar(
    "control_checkpoint", default=None
)
_workspace_run: ContextVar[tuple[str, str, str | None] | None] = ContextVar(
    "workspace_run", default=None
)


def set_workspace_run(task_id: str, run_id: str, celery_task_id: str | None = None) -> Token:
    return _workspace_run.set((task_id, run_id, celery_task_id))


def get_workspace_run() -> tuple[str, str, str | None] | None:
    return _workspace_run.get()


def reset_workspace_run(token: Token) -> None:
    _workspace_run.reset(token)


def workspace_search_params(search_params: dict) -> dict:
    """Propagate ownership to released adapters which only provide the task id."""
    current = _workspace_run.get()
    if current is None:
        return search_params
    task_id, run_id, _delivery_id = current
    if search_params.get("__workspace_task_id", task_id) != task_id:
        return search_params
    return {
        **search_params,
        "__workspace_task_id": task_id,
        "__workspace_run_id": run_id,
    }


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
