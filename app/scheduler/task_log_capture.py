"""Persist console logging records emitted while a workspace task runs."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from app.web.services.ai_collect_store import ai_collect_store

_writing_task_logs: ContextVar[bool] = ContextVar("writing_task_logs", default=False)
_STOP = object()


class WorkspaceTaskLogCapture(logging.Handler):
    """Copy task-scoped console records to ``ai_collect_task_logs``."""

    def __init__(self, task_id: str, store: Any = ai_collect_store) -> None:
        super().__init__(logging.NOTSET)
        self.task_id = task_id
        self._store = store
        self.setFormatter(logging.Formatter("%(message)s"))
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread_id: int | None = None
        self._queue: asyncio.Queue[dict[str, Any] | object] | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._loggers: list[logging.Logger] = []

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        self._queue = asyncio.Queue()
        self._writer_task = asyncio.create_task(self._write_logs())
        self._loggers = _console_loggers()
        for logger in self._loggers:
            logger.addHandler(self)

    def emit(self, record: logging.LogRecord) -> None:
        if _writing_task_logs.get() or self._loop is None or self._queue is None:
            return
        try:
            item = {
                "level": "warn" if record.levelno >= logging.WARNING else "info",
                "message": self.format(record),
                "created_at": datetime.fromtimestamp(record.created, timezone.utc),
            }
            if threading.get_ident() == self._loop_thread_id:
                self._queue.put_nowait(item)
            else:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, item)
        except Exception:
            self.handleError(record)

    async def stop(self) -> None:
        for logger in self._loggers:
            logger.removeHandler(self)
        self._loggers = []
        if self._queue is not None and self._writer_task is not None:
            await asyncio.sleep(0)
            self._queue.put_nowait(_STOP)
            await self._writer_task
        self._writer_task = None
        self._queue = None
        self._loop = None
        self._loop_thread_id = None
        super().close()

    async def _write_logs(self) -> None:
        assert self._queue is not None
        while True:
            item = await self._queue.get()
            if item is _STOP:
                return
            batch = [item]
            stopping = False
            while len(batch) < 100:
                try:
                    next_item = await asyncio.wait_for(self._queue.get(), timeout=0.1)
                except TimeoutError:
                    break
                if next_item is _STOP:
                    stopping = True
                    break
                batch.append(next_item)

            token = _writing_task_logs.set(True)
            try:
                await self._store.append_task_logs(self.task_id, batch)
            except Exception as exc:
                sys.stderr.write(f"Workspace task log write failed: {exc}\n")
            finally:
                _writing_task_logs.reset(token)
            if stopping:
                return


def _console_loggers() -> list[logging.Logger]:
    loggers = [logging.getLogger()]
    for value in logging.root.manager.loggerDict.values():
        if not isinstance(value, logging.Logger) or value.propagate:
            continue
        if any(_is_console_handler(handler) for handler in value.handlers):
            loggers.append(value)
    return loggers


def _is_console_handler(handler: logging.Handler) -> bool:
    return isinstance(handler, logging.StreamHandler) and getattr(
        handler, "stream", None
    ) in {sys.stdout, sys.stderr}
