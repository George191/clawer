from __future__ import annotations

import asyncio
import logging
import re
import sys
import threading
from concurrent.futures import Future
from contextlib import suppress
from datetime import date, datetime, timedelta
from pathlib import Path

from .fields import ADAPTER_LOGGER_NAMES, LOG_FILE_DATE_FORMAT

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
RETAIN_DAYS = 5
STREAM_HANDLER_MARKER = "_spider_adapter_stream_handler"
FILE_HANDLER_MARKER = "_spider_adapter_file_handler"
_DATED_LOG_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\.log")


class AsyncDailyAdapterFileHandler(logging.Handler):
    """Bridge standard logging calls to a daily adapter log file."""

    def __init__(self, adapter_kind: str, adapter_name: str, *, level: int) -> None:
        super().__init__(level)
        self.adapter_kind = adapter_kind
        self.adapter_name = adapter_name
        self._current_date = self._date_string()
        self._write_lock: asyncio.Lock | None = None
        self._loop = asyncio.new_event_loop()
        self._loop_ready = threading.Event()
        self._state_lock = threading.Lock()
        self._pending: set[Future[None]] = set()
        self._closing = False
        self._thread = threading.Thread(
            target=self._run_event_loop,
            name=f"adapter-log-{adapter_kind}-{adapter_name}",
            daemon=True,
        )
        self._thread.start()
        self._loop_ready.wait()

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno != logging.INFO:
            return
        if (
            getattr(record, "adapter_kind", None) != self.adapter_kind
            or getattr(record, "adapter_name", None) != self.adapter_name
        ):
            return
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return

        with self._state_lock:
            if self._closing:
                return
            future = asyncio.run_coroutine_threadsafe(
                self._write(message, record.created), self._loop
            )
            self._pending.add(future)
        future.add_done_callback(self._write_done)

    async def _write(self, message: str, timestamp: float) -> None:
        if self._write_lock is None:
            self._write_lock = asyncio.Lock()
        async with self._write_lock:
            record_date = self._date_string(timestamp)
            if record_date != self._current_date:
                self._current_date = record_date
                cleanup_adapter_logs()

            await asyncio.to_thread(self._write_line, message)

    def close(self) -> None:
        with self._state_lock:
            if self._closing:
                return
            self._closing = True
            pending = list(self._pending)

        for future in pending:
            with suppress(Exception):
                future.result()

        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()
        super().close()

    def _run_event_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        self._loop.run_forever()

    def _write_done(self, future: Future[None]) -> None:
        with self._state_lock:
            self._pending.discard(future)
        error = future.exception()
        if error is not None:
            sys.stderr.write(f"Adapter log write failed: {error}\n")

    def _log_path(self) -> Path:
        adapter_dir = LOG_DIR / self.adapter_kind / self.adapter_name
        adapter_dir.mkdir(parents=True, exist_ok=True)
        return adapter_dir / f"{self._current_date}.log"

    def _write_line(self, message: str) -> None:
        with self._log_path().open("a", encoding="utf-8") as log_file:
            log_file.write(message)
            log_file.write("\n")

    @staticmethod
    def _date_string(timestamp: float | None = None) -> str:
        value = datetime.now() if timestamp is None else datetime.fromtimestamp(timestamp)
        return value.strftime(LOG_FILE_DATE_FORMAT)


def cleanup_adapter_logs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = date.today() - timedelta(days=RETAIN_DAYS)

    for log_file in LOG_DIR.glob("adapter*.log*"):
        _unlink_log(log_file)

    legacy_dir = LOG_DIR / "registry"
    if legacy_dir.is_dir():
        for log_file in legacy_dir.glob("*.log*"):
            _unlink_log(log_file)
        with suppress(OSError):
            legacy_dir.rmdir()

    for adapter_kind in ADAPTER_LOGGER_NAMES:
        kind_dir = LOG_DIR / adapter_kind
        if not kind_dir.is_dir():
            continue
        for log_file in kind_dir.glob("*.log"):
            _unlink_log(log_file)
        for adapter_dir in kind_dir.iterdir():
            if not adapter_dir.is_dir():
                continue
            for log_file in adapter_dir.glob("*.log"):
                if not _DATED_LOG_PATTERN.fullmatch(log_file.name):
                    _unlink_log(log_file)
                    continue
                try:
                    log_date = datetime.strptime(log_file.stem, LOG_FILE_DATE_FORMAT).date()
                except ValueError:
                    _unlink_log(log_file)
                    continue
                if log_date < cutoff:
                    _unlink_log(log_file)


def build_handler(
    handler: logging.Handler,
    formatter: logging.Formatter,
    level: int,
    *filters: logging.Filter,
) -> logging.Handler:
    handler.setLevel(level)
    handler.setFormatter(formatter)
    for log_filter in filters:
        handler.addFilter(log_filter)
    return handler


def replace_handler(
    logger: logging.Logger,
    *,
    marker_name: str,
    handler: logging.Handler,
) -> None:
    for existing in list(logger.handlers):
        if getattr(existing, marker_name, False) and existing is not handler:
            logger.removeHandler(existing)
            existing.close()
    setattr(handler, marker_name, True)
    if handler not in logger.handlers:
        logger.addHandler(handler)


def _unlink_log(log_file: Path) -> None:
    try:
        log_file.unlink()
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Failed to remove legacy log file %s: %s", log_file, exc
        )
