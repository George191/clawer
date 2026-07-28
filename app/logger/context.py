from __future__ import annotations

from contextvars import ContextVar, Token

_database_only_logging: ContextVar[bool] = ContextVar(
    "database_only_logging", default=False
)


def database_only_logging_enabled() -> bool:
    return _database_only_logging.get()


def set_database_only_logging(enabled: bool) -> Token[bool]:
    return _database_only_logging.set(enabled)


def reset_database_only_logging(token: Token[bool]) -> None:
    _database_only_logging.reset(token)
