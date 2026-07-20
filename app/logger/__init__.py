from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .fields import (
    ADAPTER_LOG_FORMAT,
    ADAPTER_LOGGER_NAMES,
    DATE_FORMAT,
    AdapterContextFilter,
    Field,
)

if TYPE_CHECKING:
    from .adapter import AdapterLogger

__all__ = [
    "AdapterLogger",
    "Field",
    "configure_adapter_logging",
    "get_adapter_log_path",
    "get_adapter_logger",
    "get_logger",
    "setup_service_logging",
]


def get_adapter_log_path() -> Path:
    from .handlers import LOG_DIR

    return LOG_DIR


def get_adapter_logger(
    module_name: str,
    adapter_name: str,
    adapter_kind: str = "site",
) -> AdapterLogger:
    from .adapter import get_adapter_logger as create_adapter_logger

    return create_adapter_logger(module_name, adapter_name, adapter_kind)


def get_logger(name: str) -> logging.Logger:
    """Return a project logger for a regular application module."""
    return logging.getLogger(name)


def _resolve_log_level(level: int | str) -> int:
    if isinstance(level, int):
        return level
    return getattr(logging, level.upper(), logging.INFO)


def setup_service_logging(service: str, level: int | str, *, force: bool = False) -> None:
    resolved_level = _resolve_log_level(level)
    _reconfigure_stdio_utf8()
    logging.basicConfig(
        level=resolved_level,
        format=f"%(asctime)s [{service.upper()}] %(levelname)s %(name)s: %(message)s",
        datefmt=DATE_FORMAT,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=force,
    )
    configure_adapter_logging(resolved_level)


def configure_adapter_logging(level: int | str) -> None:
    from .adapter import set_log_level
    from .handlers import (
        STREAM_HANDLER_MARKER,
        build_handler,
        cleanup_adapter_logs,
        replace_handler,
    )

    resolved_level = _resolve_log_level(level)
    set_log_level(resolved_level)
    cleanup_adapter_logs()
    formatter = logging.Formatter(ADAPTER_LOG_FORMAT, datefmt=DATE_FORMAT)

    for logger_name in ADAPTER_LOGGER_NAMES.values():
        logger = logging.getLogger(logger_name)
        logger.setLevel(resolved_level)
        logger.propagate = False
        stream_handler = build_handler(
            logging.StreamHandler(sys.stdout),
            formatter,
            resolved_level,
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
            logger.setLevel(resolved_level)
            logger.propagate = True


def __getattr__(name: str) -> Any:
    if name == "AdapterLogger":
        from .adapter import AdapterLogger

        return AdapterLogger
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _reconfigure_stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
