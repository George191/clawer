from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOG_DIR = _REPO_ROOT / "logs"
_ADAPTER_LOG_PATH = _LOG_DIR / "adapters.log"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_ADAPTER_LOG_FORMAT = (
    "%(asctime)s [ADAPTER] %(levelname)s "
    "[%(adapter_kind)s:%(adapter_name)s] %(message)s"
)
_ADAPTER_LOGGER_NAMES = (
    "app.adapters",
    "app.anti_crawl.adapters",
)
_STREAM_HANDLER_MARKER = "_spider_adapter_stream_handler"
_FILE_HANDLER_MARKER = "_spider_adapter_file_handler"


class AdapterContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, "adapter_kind", ""):
            record.adapter_kind = (
                "proxy"
                if record.name.startswith("app.anti_crawl.adapters")
                else "site"
            )
        if not getattr(record, "adapter_name", ""):
            record.adapter_name = self._infer_adapter_name(record.name)
        if isinstance(record.msg, str):
            record.msg = self._normalize_message(record.msg)
        return True

    @staticmethod
    def _infer_adapter_name(logger_name: str) -> str:
        if logger_name in _ADAPTER_LOGGER_NAMES:
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


def get_adapter_logger(
    module_name: str,
    adapter_name: str,
    adapter_kind: str = "site",
) -> logging.LoggerAdapter:
    return logging.LoggerAdapter(
        logging.getLogger(module_name),
        {
            "adapter_name": adapter_name,
            "adapter_kind": adapter_kind,
        },
    )


def get_adapter_log_path() -> Path:
    return _ADAPTER_LOG_PATH


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
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(_ADAPTER_LOG_FORMAT, datefmt=_DATE_FORMAT)

    for logger_name in _ADAPTER_LOGGER_NAMES:
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
        logger.propagate = False
        _replace_handler(
            logger,
            marker_name=_STREAM_HANDLER_MARKER,
            handler=_build_handler(logging.StreamHandler(sys.stdout), formatter, level),
        )
        _replace_handler(
            logger,
            marker_name=_FILE_HANDLER_MARKER,
            handler=_build_handler(
                logging.FileHandler(_ADAPTER_LOG_PATH, encoding="utf-8"),
                formatter,
                level,
            ),
        )


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
        if getattr(existing, marker_name, False):
            logger.removeHandler(existing)
            existing.close()
    setattr(handler, marker_name, True)
    logger.addHandler(handler)


def _reconfigure_stdio_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
