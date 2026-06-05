"""ETL Kafka Offset 管理器 — 基于 Redis 的偏移量持久化与控制。

Redis Key 设计：
    etl:offset:{layer}:{topic}:{partition}  → <kafka_offset:int>

其中 kafka_offset 是 Kafka 分区内的全局偏移量（非从 0 开始的序号）。

功能：
- 自动保存：每条消息提交后写入 Redis
- 自动恢复：Consumer 启动时从 Redis 加载 offset 并 seek
- 手动重置：直接修改 Redis 中的值，重启 Worker 后生效
- 容错：Redis 不可用时退化为 Kafka 原生 offset 管理（不阻塞），定期重试

运维示例：
    # 查看当前消费位置
    redis-cli GET "etl:offset:rds:spider-crawler:0"

    # 重新消费所有数据（RDS 从头开始）
    redis-cli DEL "etl:offset:rds:spider-crawler:0"

    # 重新消费 ODS 层数据（从 offset 1000 继续）
    redis-cli SET "etl:offset:ods:spider-rds-processed:0" 1000
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)

REDIS_KEY_PREFIX = "etl:offset"
OFFSET_TTL_SECONDS = 30 * 24 * 3600
REDIS_RETRY_INTERVAL = 30


class OffsetManager:
    def __init__(self) -> None:
        self._redis: Any = None
        self._connected: bool = False
        self._last_retry_ts: float = 0.0

    async def _ensure_connected(self) -> None:
        if self._connected:
            return
        if not settings.redis_url:
            return

        now = time.monotonic()
        if self._last_retry_ts and (now - self._last_retry_ts) < REDIS_RETRY_INTERVAL:
            return
        self._last_retry_ts = now

        try:
            import redis.asyncio as aioredis

            if self._redis:
                try:
                    await self._redis.close()
                except Exception:
                    pass

            self._redis = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await self._redis.ping()
            self._connected = True
            logger.info("OffsetManager: Redis connected, offset persistence enabled")
        except ImportError:
            logger.warning("OffsetManager: redis-py not installed. Run: pip install redis")
        except Exception as e:
            logger.warning("OffsetManager: Redis unavailable (%s), will retry in %ds", e, REDIS_RETRY_INTERVAL)

    def _make_key(self, consumer_group: str, topic: str, partition: int) -> str:
        return f"{REDIS_KEY_PREFIX}:{consumer_group}:{topic}:{partition}"

    async def save_offset(
        self,
        consumer_group: str,
        topic: str,
        partition: int,
        offset: int,
    ) -> None:
        await self._ensure_connected()
        if not self._connected or not self._redis:
            return

        try:
            key = self._make_key(consumer_group, topic, partition)
            await self._redis.set(key, str(offset), ex=OFFSET_TTL_SECONDS)
            logger.debug("OffsetManager: saved %s = %d (kafka_offset)", key, offset)
        except Exception as e:
            logger.warning("OffsetManager: save_offset failed: %s", e)
            self._connected = False

    async def load_offsets(
        self,
        consumer_group: str,
        topic: str,
    ) -> dict[int, int]:
        await self._ensure_connected()
        if not self._connected or not self._redis:
            return {}

        result: dict[int, int] = {}
        try:
            pattern = f"{REDIS_KEY_PREFIX}:{consumer_group}:{topic}:*"
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(cursor=cursor, match=pattern, count=50)
                for key in keys:
                    try:
                        partition = int(key.rsplit(":", 1)[-1])
                        val = await self._redis.get(key)
                        if val is not None:
                            result[partition] = int(val)
                    except (ValueError, TypeError):
                        pass
                if cursor == 0:
                    break
        except Exception as e:
            logger.warning("OffsetManager: load_offsets failed: %s", e)
            self._connected = False
            return {}

        if result:
            logger.info(
                "OffsetManager: loaded %d offsets for %s/%s = %s",
                len(result), consumer_group, topic,
                {str(k): v for k, v in sorted(result.items())},
            )
        return result

    async def reset_offsets(self, consumer_group: str, topic: str) -> int:
        await self._ensure_connected()
        if not self._connected or not self._redis:
            return 0

        try:
            pattern = f"{REDIS_KEY_PREFIX}:{consumer_group}:{topic}:*"
            deleted = 0
            cursor = 0
            while True:
                cursor, keys = await self._redis.scan(cursor=cursor, match=pattern, count=50)
                if keys:
                    deleted += await self._redis.delete(*keys)
                if cursor == 0:
                    break
            logger.info("OffsetManager: reset %d keys for %s/%s", deleted, consumer_group, topic)
            return deleted
        except Exception as e:
            logger.warning("OffsetManager: reset_offsets failed: %s", e)
            self._connected = False
            return 0


_offset_manager: OffsetManager | None = None


def get_offset_manager() -> OffsetManager:
    global _offset_manager
    if _offset_manager is None:
        _offset_manager = OffsetManager()
    return _offset_manager