"""Postgres 异步客户端 — 连接池管理和 SQL 执行。

基于 asyncpg 和 SQLAlchemy 异步引擎。
支持启动重试、连接诊断日志。
"""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import text
from sqlalchemy.exc import OperationalError, DBAPIError, InterfaceError, TimeoutError as SATimeoutError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config.settings import settings

logger = logging.getLogger(__name__)

PG_CONNECT_RETRY_WAIT = 3
PG_CONNECT_MAX_RETRY = 30
PG_CONNECT_MAX_RETRY_DELAY = 60

_DDL_STMT_SEP = re.compile(r";\s*\n\s*")

# SQLAlchemy 2.0+ TimeoutError 兼容
try:
    from sqlalchemy.exc import TimeoutError as SA2TimeoutError
except ImportError:
    SA2TimeoutError = SATimeoutError


class PostgresClient:
    def __init__(self) -> None:
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker | None = None
        self._connected = False

    async def connect(self, max_retries: int = PG_CONNECT_MAX_RETRY) -> None:
        if self._engine is not None and self._connected:
            return

        masked_url = _mask_url(settings.pg_url)
        retryable = (
            OperationalError, DBAPIError, InterfaceError,
            ConnectionRefusedError, OSError, SATimeoutError, SA2TimeoutError,
        )

        attempt = 0
        last_error: Exception | None = None

        while attempt < max_retries:
            try:
                self._engine = create_async_engine(
                    settings.pg_url,
                    pool_size=settings.pg_pool_min,
                    max_overflow=settings.pg_pool_max - settings.pg_pool_min,
                    pool_pre_ping=True,
                    pool_recycle=3600,
                    connect_args={
                        "timeout": 10,
                        "command_timeout": 10,
                    },
                )
                self._session_factory = async_sessionmaker(
                    self._engine,
                    expire_on_commit=False,
                )

                async with self._engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
                self._connected = True
                logger.info("Connected to Postgres: %s (attempt %d)", masked_url, attempt + 1)
                return

            except retryable as e:
                last_error = e
                self._connected = False
                if self._engine:
                    await self._engine.dispose()
                    self._engine = None
                    self._session_factory = None

                delay = min(PG_CONNECT_RETRY_WAIT * (2 ** min(attempt, 5)), PG_CONNECT_MAX_RETRY_DELAY)
                logger.warning(
                    "Postgres not ready (attempt %d/%d): %s. Retrying in %ds... [%s]",
                    attempt + 1, max_retries, e, delay, masked_url,
                )
                await asyncio.sleep(delay)
                attempt += 1

        masked_error = _mask_password(str(last_error))
        logger.error(
            "Postgres connection FAILED after %d attempts at %s: %s",
            max_retries, masked_url, masked_error,
        )
        raise ConnectionError(
            f"无法连接到 Postgres ({masked_url})，已重试 {max_retries} 次。"
            f"最后错误: {masked_error}"
        ) from last_error

    async def close(self) -> None:
        if self._engine:
            await self._engine.dispose()
            self._engine = None
            self._session_factory = None
            self._connected = False
            logger.info("Postgres connection closed")

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        if not self._session_factory:
            await self.connect()
        session = self._session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    @asynccontextmanager
    async def locked_transaction(
        self, lock_key: str,
    ) -> AsyncIterator[AsyncSession]:
        """事务上下文，带有 advisory lock，用于安全的合并操作。

        同一 lock_key 的并发操作会被序列化，避免 check-then-insert 竞态。
        锁在事务提交/回滚时自动释放（pg_advisory_xact_lock）。
        """
        if not self._session_factory:
            await self.connect()
        session = self._session_factory()
        try:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": lock_key},
            )
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        async with self.session() as session:
            await session.execute(text(sql), params or {})

    async def execute_many(self, sql: str, params_list: list[dict[str, Any]]) -> None:
        async with self.session() as session:
            for params in params_list:
                await session.execute(text(sql), params)

    async def fetch_all(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        async with self.session() as session:
            result = await session.execute(text(sql), params or {})
            rows = result.mappings().all()
            return [dict(r) for r in rows]

    async def fetch_one(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        async with self.session() as session:
            result = await session.execute(text(sql), params or {})
            row = result.mappings().first()
            return dict(row) if row else None

    async def init_schema(self, ddl_blocks: list[str]) -> None:
        await self.connect()
        async with self.session() as session:
            for block in ddl_blocks:
                stmts = [s.strip() for s in _DDL_STMT_SEP.split(block) if s.strip()]
                for stmt in stmts:
                    await session.execute(text(stmt))
        logger.info("ETL schema initialized (RDS/ODS/TASK schema created)")


_pg_client: PostgresClient | None = None


def get_pg_client() -> PostgresClient:
    global _pg_client
    if _pg_client is None:
        _pg_client = PostgresClient()
    return _pg_client


def _mask_url(url: str) -> str:
    masked = re.sub(r"://[^@]+@", "://***:***@", url)
    return masked


def _mask_password(text: str) -> str:
    for pattern in (
        r'(password[=:]\s*["\']?)[^\s"\'&;,\n]+',
        r'(://[^:]+:)[^@\n]+(@)',
    ):
        text = re.sub(pattern, r"\1***\2" if r"\2" in pattern else r"\1***", text)
    return text