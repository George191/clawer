"""One UTC message format for persisted task events and captured records."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

_SECONDS = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
_ACTOR = r"[A-Za-z][A-Za-z0-9_.:-]{0,127}"
_CANONICAL = re.compile(rf"^\[{_SECONDS},\d{{3}}: (?:DEBUG|INFO|WARNING|ERROR|CRITICAL|OK)/{_ACTOR}\] ")
_CONNECTION_USERINFO = re.compile(
    r"\b((?:mongodb(?:\+srv)?|postgres(?:ql)?(?:\+[a-z0-9_]+)?|rediss?)://)[^\s/?#]*@",
    re.IGNORECASE,
)
_LEVELS = {"WARN": "WARNING", "FATAL": "CRITICAL"}


def redact_connection_userinfo(message: str) -> str:
    return _CONNECTION_USERINFO.sub(r"\1***@", message)


def task_log_time(created_at: datetime | float | str | None = None) -> datetime:
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if isinstance(created_at, datetime):
        return created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at.astimezone(timezone.utc)
    return datetime.now(timezone.utc) if created_at is None else datetime.fromtimestamp(created_at, timezone.utc)


def format_task_log_message(
    message: str,
    level: int | str,
    executor: str = "System",
    created_at: datetime | float | str | None = None,
) -> str:
    prefix = _CANONICAL.match(message)
    body = redact_connection_userinfo(message[prefix.end():] if prefix else message)
    if prefix:
        return message[:prefix.end()] + body
    event_time = task_log_time(created_at)
    stamp = event_time.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    actor = executor if isinstance(executor, str) and re.fullmatch(_ACTOR, executor) else "System"
    severity = logging.getLevelName(level) if isinstance(level, int) else level.upper()
    severity = _LEVELS.get(severity, severity)
    if severity not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "OK"}:
        severity = "INFO"
    return f"[{stamp}: {severity}/{actor}] {body}"
