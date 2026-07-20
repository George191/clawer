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

from aiologger import Logger as AsyncLogger
from aiologger.handlers.files import AsyncFileHandler
from aiologger.levels import LogLevel

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _REPO_ROOT / "logs"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LOG_FILE_DATE_FORMAT = "%Y-%m-%d"
_ADAPTER_LOG_FORMAT = (
    "%(asctime)s [ADAPTER] %(levelname)s [%(adapter_kind)s:%(adapter_name)s] %(message)s"
)
_ADAPTER_LOGGER_NAMES = {
    "site": "app.adapters",
    "proxy": "app.anti_crawl.adapters",
}
_STREAM_HANDLER_MARKER = "_spider_adapter_stream_handler"
_FILE_HANDLER_MARKER = "_spider_adapter_file_handler"
_ADAPTER_FILE_HANDLERS: dict[str, AsyncDailyAdapterFileHandler] = {}
_ADAPTER_LOG_LEVEL = logging.DEBUG
_RETAIN_DAYS = 5
_DATED_LOG_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}\.log")


class AdapterContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "adapter_kind", ""):
            record.adapter_kind = (
                "proxy" if record.name.startswith("app.anti_crawl.adapters") else "site"
            )
        if not getattr(record, "adapter_name", ""):
            record.adapter_name = self._infer_adapter_name(record.name)
        if isinstance(record.msg, str):
            record.msg = self._normalize_message(record.msg)
        return True

    @staticmethod
    def _infer_adapter_name(logger_name: str) -> str:
        if logger_name in _ADAPTER_LOGGER_NAMES.values():
            return "registry"
        module_name = logger_name.rsplit(".", 1)[-1]
        if module_name == "__init__":
            return "registry"
        return module_name or "unknown"

    @staticmethod
    def _normalize_message(message: str) -> str:
        cleaned = re.sub(r"^\[[^\]]+\]\s*", "", message)
        cleaned = re.sub(r"^[A-Za-z0-9_]+Adapter:\s*", "", cleaned)
        cleaned = re.sub(r"^[^\w]+", "", cleaned)
        return cleaned or message


class ExactInfoFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno == logging.INFO


class AsyncDailyAdapterFileHandler(logging.Handler):
    """Bridge standard logging calls to an aiologger daily file writer."""

    def __init__(self, adapter_kind: str, *, level: int) -> None:
        super().__init__(level)
        self.adapter_kind = adapter_kind
        self._current_date = self._date_string()
        self._async_logger: AsyncLogger | None = None
        self._write_lock: asyncio.Lock | None = None
        self._loop = asyncio.new_event_loop()
        self._loop_ready = threading.Event()
        self._state_lock = threading.Lock()
        self._pending: set[Future[None]] = set()
        self._closing = False
        self._thread = threading.Thread(
            target=self._run_event_loop,
            name=f"adapter-log-{adapter_kind}",
            daemon=True,
        )
        self._thread.start()
        self._loop_ready.wait()

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno != logging.INFO:
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
                await self._shutdown_writer()
                self._current_date = record_date
                _cleanup_adapter_logs()

            if self._async_logger is None:
                self._async_logger = AsyncLogger(
                    name=f"adapter.{self.adapter_kind}", level=LogLevel.INFO
                )
                self._async_logger.add_handler(
                    AsyncFileHandler(str(self._log_path()), encoding="utf-8")
                )
            await self._async_logger.info(message)

    def close(self) -> None:
        with self._state_lock:
            if self._closing:
                return
            self._closing = True
            pending = list(self._pending)

        for future in pending:
            with suppress(Exception):
                future.result()

        shutdown = asyncio.run_coroutine_threadsafe(self._shutdown_writer(), self._loop)
        with suppress(Exception):
            shutdown.result()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()
        super().close()

    async def _shutdown_writer(self) -> None:
        if self._async_logger is not None:
            await self._async_logger.shutdown()
            self._async_logger = None

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
        kind_dir = _LOG_DIR / self.adapter_kind
        kind_dir.mkdir(parents=True, exist_ok=True)
        return kind_dir / f"{self._current_date}.log"

    @staticmethod
    def _date_string(timestamp: float | None = None) -> str:
        value = datetime.now() if timestamp is None else datetime.fromtimestamp(timestamp)
        return value.strftime(_LOG_FILE_DATE_FORMAT)


def _cleanup_adapter_logs() -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = date.today() - timedelta(days=_RETAIN_DAYS)

    for log_file in _LOG_DIR.glob("adapter*.log*"):
        _unlink_log(log_file)

    legacy_dir = _LOG_DIR / "registry"
    if legacy_dir.is_dir():
        for log_file in legacy_dir.glob("*.log*"):
            _unlink_log(log_file)
        with suppress(OSError):
            legacy_dir.rmdir()

    for adapter_kind in _ADAPTER_LOGGER_NAMES:
        kind_dir = _LOG_DIR / adapter_kind
        if not kind_dir.is_dir():
            continue
        for log_file in kind_dir.glob("*.log"):
            if not _DATED_LOG_PATTERN.fullmatch(log_file.name):
                _unlink_log(log_file)
                continue
            try:
                log_date = datetime.strptime(log_file.stem, _LOG_FILE_DATE_FORMAT).date()
            except ValueError:
                _unlink_log(log_file)
                continue
            if log_date < cutoff:
                _unlink_log(log_file)


def _unlink_log(log_file: Path) -> None:
    try:
        log_file.unlink()
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Failed to remove legacy log file %s: %s", log_file, exc
        )


def _get_adapter_file_handler(adapter_kind: str, level: int) -> logging.Handler:
    try:
        logger_name = _ADAPTER_LOGGER_NAMES[adapter_kind]
    except KeyError as exc:
        raise ValueError(f"Unsupported adapter kind: {adapter_kind}") from exc

    handler = _ADAPTER_FILE_HANDLERS.get(adapter_kind)
    if handler is None:
        _cleanup_adapter_logs()
        handler = AsyncDailyAdapterFileHandler(adapter_kind, level=level)
        handler.setFormatter(logging.Formatter(_ADAPTER_LOG_FORMAT, datefmt=_DATE_FORMAT))
        handler.addFilter(AdapterContextFilter())
        handler.addFilter(ExactInfoFilter())
        setattr(handler, _FILE_HANDLER_MARKER, True)
        _ADAPTER_FILE_HANDLERS[adapter_kind] = handler
    else:
        handler.setLevel(level)

    _replace_handler(
        logging.getLogger(logger_name),
        marker_name=_FILE_HANDLER_MARKER,
        handler=handler,
    )
    return handler


def get_adapter_logger(
    module_name: str,
    adapter_name: str,
    adapter_kind: str = "site",
) -> logging.LoggerAdapter:
    logger = logging.getLogger(module_name)
    _get_adapter_file_handler(adapter_kind, _ADAPTER_LOG_LEVEL)
    logger.setLevel(_ADAPTER_LOG_LEVEL)

    return logging.LoggerAdapter(
        logger,
        {
            "adapter_name": adapter_name,
            "adapter_kind": adapter_kind,
        },
    )


def get_adapter_log_path() -> Path:
    return _LOG_DIR


def setup_service_logging(service: str, level: int, *, force: bool = False) -> None:
    _reconfigure_stdio_utf8()
    logging.basicConfig(
        level=level,
        format=f"%(asctime)s [{service.upper()}] %(levelname)s %(name)s: %(message)s",
        datefmt=_DATE_FORMAT,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=force,
    )
    configure_adapter_logging(level)


def configure_adapter_logging(level: int) -> None:
    global _ADAPTER_LOG_LEVEL

    _ADAPTER_LOG_LEVEL = level
    _cleanup_adapter_logs()
    formatter = logging.Formatter(_ADAPTER_LOG_FORMAT, datefmt=_DATE_FORMAT)

    for adapter_kind, logger_name in _ADAPTER_LOGGER_NAMES.items():
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        logger.propagate = False

        stream_handler = _build_handler(logging.StreamHandler(sys.stdout), formatter, level)
        _replace_handler(
            logger,
            marker_name=_STREAM_HANDLER_MARKER,
            handler=stream_handler,
        )
        _get_adapter_file_handler(adapter_kind, level)

    for name in logging.root.manager.loggerDict:
        if any(
            name.startswith(f"{logger_name}.") for logger_name in _ADAPTER_LOGGER_NAMES.values()
        ):
            logger = logging.getLogger(name)
            logger.setLevel(level)
            logger.propagate = True


def _build_handler(
    handler: logging.Handler,
    formatter: logging.Formatter,
    level: int,
) -> logging.Handler:
    handler.setLevel(level)
    handler.setFormatter(formatter)
    handler.addFilter(AdapterContextFilter())
    return handler


def _replace_handler(
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


def _reconfigure_stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
