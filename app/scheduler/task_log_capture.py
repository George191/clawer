"""Persist console logging records emitted while a workspace task runs."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from app.logger.context import reset_database_only_logging, set_database_only_logging
from app.web.services.ai_collect_store import ai_collect_store

_writing_task_logs: ContextVar[bool] = ContextVar("writing_task_logs", default=False)
_STOP = object()
_CELERY_CONSOLE_FORMATTER = logging.Formatter(
    "[%(asctime)s: %(levelname)s/%(processName)s] %(message)s"
)


class WorkspaceTaskLogCapture(logging.Handler):
    """Copy task-scoped console records to ``ai_collect_task_logs``."""

    def __init__(self, task_id: str, run_id: str, store: Any = ai_collect_store) -> None:
        super().__init__(logging.NOTSET)
        self.task_id = task_id
        self.run_id = run_id
        self._store = store
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread_id: int | None = None
        self._queue: asyncio.Queue[dict[str, Any] | object] | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._loggers: list[logging.Logger] = []
        self._formatters: dict[str, logging.Formatter] = {}
        self._suppressed_handlers: list[tuple[logging.Logger, logging.Handler]] = []
        self._database_only_token = None

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._loop_thread_id = threading.get_ident()
        self._queue = asyncio.Queue()
        self._writer_task = asyncio.create_task(self._write_logs())
        self._database_only_token = set_database_only_logging(True)
        all_loggers = _all_loggers()
        self._formatters = _console_formatters(all_loggers)
        for logger in all_loggers:
            for handler in list(logger.handlers):
                if _is_task_output_handler(handler):
                    logger.removeHandler(handler)
                    self._suppressed_handlers.append((logger, handler))
        self._loggers = [
            logger for logger in all_loggers if logger is logging.getLogger() or not logger.propagate
        ]
        for logger in self._loggers:
            logger.addHandler(self)

    def emit(self, record: logging.LogRecord) -> None:
        if _writing_task_logs.get() or self._loop is None or self._queue is None:
            return
        try:
            item = {
                "run_id": self.run_id,
                "level": "warn" if record.levelno >= logging.WARNING else "info",
                "message": _format_console_record(record, self._formatters),
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
        try:
            if self._queue is not None and self._writer_task is not None:
                await asyncio.sleep(0)
                self._queue.put_nowait(_STOP)
                await self._writer_task
        finally:
            for logger, handler in self._suppressed_handlers:
                logger.addHandler(handler)
            self._suppressed_handlers = []
            self._formatters = {}
            if self._database_only_token is not None:
                reset_database_only_logging(self._database_only_token)
                self._database_only_token = None
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
                await self._store.append_task_logs(self.task_id, [item])
            except Exception as exc:
                sys.stderr.write(f"Workspace task log write failed: {exc}\n")
            finally:
                _writing_task_logs.reset(token)


def _all_loggers() -> list[logging.Logger]:
    loggers = [logging.getLogger()]
    for value in logging.root.manager.loggerDict.values():
        if isinstance(value, logging.Logger):
            loggers.append(value)
    return loggers


def _is_console_handler(handler: logging.Handler) -> bool:
    return isinstance(handler, logging.StreamHandler) and not isinstance(
        handler, logging.FileHandler
    )


def _is_task_output_handler(handler: logging.Handler) -> bool:
    return isinstance(handler, logging.StreamHandler) or bool(
        getattr(handler, "_spider_adapter_file_handler", False)
    )


def _console_formatters(
    loggers: list[logging.Logger],
) -> dict[str, logging.Formatter]:
    formatters: dict[str, logging.Formatter] = {}
    for logger in loggers:
        for handler in logger.handlers:
            if _is_console_handler(handler):
                formatters[logger.name] = handler.formatter or logging.Formatter(
                    "%(message)s"
                )
                break
    return formatters


def _format_console_record(
    record: logging.LogRecord,
    formatters: dict[str, logging.Formatter] | None = None,
) -> str:
    logger = logging.getLogger(record.name)
    while True:
        formatter = (formatters or {}).get(logger.name)
        if formatter is not None:
            return formatter.format(record)
        if formatters is None:
            for handler in logger.handlers:
                if _is_console_handler(handler):
                    formatter = handler.formatter or logging.Formatter("%(message)s")
                    return formatter.format(record)
        if not logger.propagate or logger.parent is None:
            break
        logger = logger.parent
    return _CELERY_CONSOLE_FORMATTER.format(record)
