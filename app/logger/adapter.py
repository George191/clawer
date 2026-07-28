from __future__ import annotations

import logging

from .context import database_only_logging_enabled
from .fields import (
    ADAPTER_LOG_FORMAT,
    ADAPTER_LOGGER_NAMES,
    DATE_FORMAT,
    AdapterContextFilter,
    ExactInfoFilter,
)
from .handlers import (
    FILE_HANDLER_MARKER,
    AsyncDailyAdapterFileHandler,
    cleanup_adapter_logs,
    replace_handler,
)

_FILE_HANDLERS: dict[tuple[str, str], AsyncDailyAdapterFileHandler] = {}
_LOG_LEVEL = logging.DEBUG


class AdapterLogger(logging.LoggerAdapter):
    """Bind adapter type and template name to standard logging records."""


def get_adapter_logger(
    module_name: str,
    adapter_name: str,
    adapter_kind: str = "site",
) -> AdapterLogger:
    logger = logging.getLogger(module_name)
    if not database_only_logging_enabled():
        handler = _get_file_handler(adapter_kind, adapter_name)
        replace_handler(logger, marker_name=FILE_HANDLER_MARKER, handler=handler)
    logger.setLevel(_LOG_LEVEL)
    return AdapterLogger(
        logger,
        {
            "adapter_name": adapter_name,
            "adapter_kind": adapter_kind,
        },
    )


def set_log_level(level: int) -> None:
    global _LOG_LEVEL

    _LOG_LEVEL = level
    for handler in _FILE_HANDLERS.values():
        handler.setLevel(level)


def _get_file_handler(
    adapter_kind: str,
    adapter_name: str,
) -> AsyncDailyAdapterFileHandler:
    if adapter_kind not in ADAPTER_LOGGER_NAMES:
        raise ValueError(f"Unsupported adapter kind: {adapter_kind}")

    handler_key = (adapter_kind, adapter_name)
    handler = _FILE_HANDLERS.get(handler_key)
    if handler is None:
        cleanup_adapter_logs()
        handler = AsyncDailyAdapterFileHandler(
            adapter_kind,
            adapter_name,
            level=_LOG_LEVEL,
        )
        handler.setFormatter(logging.Formatter(ADAPTER_LOG_FORMAT, datefmt=DATE_FORMAT))
        handler.addFilter(AdapterContextFilter())
        handler.addFilter(ExactInfoFilter())
        setattr(handler, FILE_HANDLER_MARKER, True)
        _FILE_HANDLERS[handler_key] = handler
    return handler
