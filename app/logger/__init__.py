from __future__ import annotations

import logging
import sys
from pathlib import Path

from .adapter import AdapterLogger, get_adapter_logger, set_log_level
from .fields import (
    ADAPTER_LOG_FORMAT,
    ADAPTER_LOGGER_NAMES,
    DATE_FORMAT,
    AdapterContextFilter,
    Field,
)
from .handlers import (
    LOG_DIR,
    STREAM_HANDLER_MARKER,
    build_handler,
    cleanup_adapter_logs,
    replace_handler,
)

__all__ = [
    "AdapterLogger",
    "Field",
    "configure_adapter_logging",
    "get_adapter_log_path",
    "get_adapter_logger",
    "setup_service_logging",
]


def get_adapter_log_path() -> Path:
    return LOG_DIR


def setup_service_logging(service: str, level: int, *, force: bool = False) -> None:
    _reconfigure_stdio_utf8()
    logging.basicConfig(
        level=level,
        format=f"%(asctime)s [{service.upper()}] %(levelname)s %(name)s: %(message)s",
        datefmt=DATE_FORMAT,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=force,
    )
    configure_adapter_logging(level)


def configure_adapter_logging(level: int) -> None:
    set_log_level(level)
    cleanup_adapter_logs()
    formatter = logging.Formatter(ADAPTER_LOG_FORMAT, datefmt=DATE_FORMAT)

    for logger_name in ADAPTER_LOGGER_NAMES.values():
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        logger.propagate = False
        stream_handler = build_handler(
            logging.StreamHandler(sys.stdout),
            formatter,
            level,
            AdapterContextFilter(),
        )
        replace_handler(
            logger,
            marker_name=STREAM_HANDLER_MARKER,
            handler=stream_handler,
        )

    for name in logging.root.manager.loggerDict:
        if any(name.startswith(f"{logger_name}.") for logger_name in ADAPTER_LOGGER_NAMES.values()):
            logger = logging.getLogger(name)
            logger.setLevel(level)
            logger.propagate = True


def _reconfigure_stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
