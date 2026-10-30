"""Persist console logging records emitted while an automation task runs."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from contextvars import ContextVar
from typing import Any

from app.web.services.automation_task_store import automation_task_store

_writing_task_logs: ContextVar[bool] = ContextVar("writing_task_logs", default=False)
_workspace_log_capture: ContextVar[WorkspaceTaskLogCapture | None] = ContextVar(
    "workspace_log_capture", default=None,
)
_STOP = object()
# Progress lines are rewritten in place so a download shows ONE live bar row
# instead of one row per refresh; the finished line rewrites that same row so
# the row's message simply flips to the finished summary when the download ends.
PROGRESS_LINE_PREFIXES = ("download progress:", "download finished:")


class TaskLogCapture(logging.Handler):
    """Copy task-scoped console records to ``automation_task_logs``."""

    executor = "Scheduler"

    def __init__(self, task_id: str, run_id: str, store: Any = automation_task_store) -> None:
        super().__init__(logging.NOTSET)
        self.task_id = task_id
        self.run_id = run_id
        self._store = store
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread_id: int | None = None
        self._queue: asyncio.Queue[dict[str, Any] | object] | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._loggers: list[logging.Logger] = []
        self._progress_log_id: int | None = None

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        self._queue = asyncio.Queue()
        self._writer_task = asyncio.create_task(self._write_logs())
        all_loggers = _all_loggers()
        self._loggers = [
            logger for logger in all_loggers if logger is logging.getLogger() or not logger.propagate
        ]
        for logger in self._loggers:
            logger.addHandler(self)

    def emit(self, record: logging.LogRecord) -> None:
        if _writing_task_logs.get() or self._loop is None or self._queue is None:
            return
        try:
            item = self._log_item(record)
            if threading.get_ident() == self._loop_thread_id:
                self._queue.put_nowait(item)
            else:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, item)
        except Exception:
            self.handleError(record)

    def _log_item(self, record: logging.LogRecord) -> dict[str, Any]:
        return {
            "level": record.levelno,
            "message": logging.Formatter("%(message)s").format(record),
            "created_at": record.created,
        }

    async def stop(self) -> None:
        for logger in self._loggers:
            logger.removeHandler(self)
        self._loggers = []
        try:
            if self._queue is not None and self._writer_task is not None:
                await asyncio.sleep(0)
                self._queue.put_nowait(_STOP)
                await self._writer_task
        finally:
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
            token = _writing_task_logs.set(True)
            try:
                await self._write_item(item)
            except Exception as exc:
                sys.stderr.write(f"Automation task log write failed: {exc}\n")
            finally:
                _writing_task_logs.reset(token)

    async def _write_item(self, item: dict[str, Any]) -> None:
        progress = any(marker in item["message"] for marker in PROGRESS_LINE_PREFIXES)
        inserted = await self._store.write_task_log(
            self.task_id, self.run_id, self.executor, item["level"], item["message"],
            created_at=item["created_at"],
            log_id=self._progress_log_id if progress else None,
        )
        if progress and inserted:
            self._progress_log_id = inserted[0].get("id")


class WorkspaceTaskLogCapture(TaskLogCapture):
    """Capture only this crawl's context and delegate formatting to the store."""

    executor = "Crawler"

    def start(self) -> None:
        self._capture_token = _workspace_log_capture.set(self)
        super().start()

    def emit(self, record: logging.LogRecord) -> None:
        if _workspace_log_capture.get() is self:
            super().emit(record)

    async def stop(self) -> None:
        try:
            await super().stop()
        finally:
            _workspace_log_capture.reset(self._capture_token)


def _all_loggers() -> list[logging.Logger]:
    loggers = [logging.getLogger()]
    for value in logging.root.manager.loggerDict.values():
        if isinstance(value, logging.Logger):
            loggers.append(value)
    return loggers
