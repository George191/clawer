from __future__ import annotations

import logging
import re
from enum import Enum


class Field(str, Enum):
    adapter_kind = "adapter_kind"
    adapter_name = "adapter_name"


DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_FILE_DATE_FORMAT = "%Y-%m-%d"
ADAPTER_LOG_FORMAT = (
    "%(asctime)s [ADAPTER] %(levelname)s [%(adapter_kind)s:%(adapter_name)s] %(message)s"
)
ADAPTER_LOGGER_NAMES = {
    "site": "app.adapters",
    "proxy": "app.anti_crawl.adapters",
}


class AdapterContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not getattr(record, Field.adapter_kind, ""):
            record.adapter_kind = (
                "proxy" if record.name.startswith("app.anti_crawl.adapters") else "site"
            )
        if not getattr(record, Field.adapter_name, ""):
            record.adapter_name = self._infer_adapter_name(record.name)
        if isinstance(record.msg, str):
            record.msg = self._normalize_message(record.msg)
        return True

    @staticmethod
    def _infer_adapter_name(logger_name: str) -> str:
        if logger_name in ADAPTER_LOGGER_NAMES.values():
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
