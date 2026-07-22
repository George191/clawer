"""Shared async Redis connection lifecycle for checkpoint stores."""

from __future__ import annotations

import time
from contextlib import suppress
from typing import Any

from app.logger import get_logger

logger = get_logger(__name__)


class RedisConnection:
    """Lazy Redis connection with throttled reconnects and safe shutdown."""

    def __init__(
        self,
        redis_url: str,
        client: Any = None,
        retry_interval: float = 30,
        socket_connect_timeout: float = 3,
    ) -> None:
        self._redis_url = redis_url
        self._client = client
        self._owns_client = client is None
        self._connected = client is not None
        self._retry_interval = retry_interval
        self._socket_connect_timeout = socket_connect_timeout
        self._last_retry_ts = 0.0

    @property
    def client(self) -> Any:
        return self._client if self._connected else None

    def mark_unavailable(self) -> None:
        self._connected = False

    async def ensure_connected(self) -> Any | None:
        if self._connected and self._client is not None:
            return self._client
        if not self._redis_url:
            return None

        now = time.monotonic()
        if self._last_retry_ts and now - self._last_retry_ts < self._retry_interval:
            return None
        self._last_retry_ts = now

        try:
            import redis.asyncio as aioredis

            if self._client is not None and self._owns_client:
                with suppress(Exception):
                    await self._client.aclose()

            self._client = aioredis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=self._socket_connect_timeout,
            )
            await self._client.ping()
            self._connected = True
            return self._client
        except ImportError:
            logger.warning("Redis client is unavailable: redis-py is not installed")
        except Exception as exc:
            logger.warning("Redis connection failed: %s", exc)
        self._connected = False
        return None

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            try:
                close = getattr(self._client, "aclose", None)
                if close is None:
                    close = self._client.close
                result = close()
                if result is not None:
                    await result
            except Exception as exc:
                logger.warning("Redis close failed: %s", exc)
        self._client = None
        self._connected = False
