"""ETL Kafka Offset 管理器 — 基于 Redis 的偏移量持久化与控制。

Redis Key 设计：
    offset:{layer}:{topic}:{partition}  → <kafka_offset:int>

其中 kafka_offset 是 Kafka 分区内的全局偏移量（非从 0 开始的序号）。

功能：
- 自动保存：每条消息提交后写入 Redis
- 自动恢复：Consumer 启动时从 Redis 加载 offset 并 seek
- 手动重置：直接修改 Redis 中的值，重启 Worker 后生效
- 容错：Redis 不可用时退化为 Kafka 原生 offset 管理（不阻塞），定期重试

运维示例：
    # 查看当前消费位置
    redis-cli -n 2 GET "offset:rds:spider-crawler:0"

    # 重新消费所有数据（RDS 从头开始）
    redis-cli -n 2 DEL "offset:rds:spider-crawler:0"

    # 重新消费 ODS 层数据（从 offset 1000 继续）
    redis-cli -n 2 SET "offset:ods:spider-rds-processed:0" 1000
"""

from __future__ import annotations

from typing import Any

from app.base.redis_connection import RedisConnection
from app.config.settings import settings
from app.logger import get_logger

logger = get_logger(__name__)

REDIS_KEY_PREFIX = "offset"
OFFSET_TTL_SECONDS = 30 * 24 * 3600
REDIS_RETRY_INTERVAL = 30


class OffsetManager:
    def __init__(self) -> None:
        self._connection = RedisConnection(
            settings.etl_redis_url,
            retry_interval=REDIS_RETRY_INTERVAL,
        )

    async def _get_redis(self) -> Any | None:
        return await self._connection.ensure_connected()

    def _make_key(self, consumer_group: str, topic: str, partition: int) -> str:
        return f"{REDIS_KEY_PREFIX}:{consumer_group}:{topic}:{partition}"

    async def save_offset(
        self,
        consumer_group: str,
        topic: str,
        partition: int,
        offset: int,
    ) -> None:
        redis = await self._get_redis()
        if redis is None:
            return

        try:
            key = self._make_key(consumer_group, topic, partition)
            await redis.set(key, str(offset), ex=OFFSET_TTL_SECONDS)
            logger.debug("OffsetManager: saved %s = %d (kafka_offset)", key, offset)
        except Exception as e:
            logger.warning("OffsetManager: save_offset failed: %s", e)
            self._connection.mark_unavailable()

    async def set_offset(
        self,
        consumer_group: str,
        topic: str,
        partition: int,
        offset: int,
    ) -> bool:
        """Persist an operator-approved resume position for the next worker start."""
        redis = await self._get_redis()
        if redis is None:
            return False
        try:
            await redis.set(
                self._make_key(consumer_group, topic, partition),
                str(offset),
                ex=OFFSET_TTL_SECONDS,
            )
            return True
        except Exception as e:
            logger.warning("OffsetManager: set_offset failed: %s", e)
            self._connection.mark_unavailable()
            return False

    async def load_offsets(
        self,
        consumer_group: str,
        topic: str,
    ) -> dict[int, int]:
        redis = await self._get_redis()
        if redis is None:
            return {}

        result: dict[int, int] = {}
        try:
            pattern = f"{REDIS_KEY_PREFIX}:{consumer_group}:{topic}:*"
            cursor = 0
            while True:
                cursor, keys = await redis.scan(cursor=cursor, match=pattern, count=50)
                for key in keys:
                    try:
                        partition = int(key.rsplit(":", 1)[-1])
                        val = await redis.get(key)
                        if val is not None:
                            result[partition] = int(val)
                    except (ValueError, TypeError):
                        pass
                if cursor == 0:
                    break
        except Exception as e:
            logger.warning("OffsetManager: load_offsets failed: %s", e)
            self._connection.mark_unavailable()
            return {}

        if result:
            logger.info(
                "OffsetManager: loaded %d offsets for %s/%s = %s",
                len(result), consumer_group, topic,
                {str(k): v for k, v in sorted(result.items())},
            )
        return result

    async def reset_offsets(self, consumer_group: str, topic: str) -> int:
        redis = await self._get_redis()
        if redis is None:
            return 0

        try:
            pattern = f"{REDIS_KEY_PREFIX}:{consumer_group}:{topic}:*"
            deleted = 0
            cursor = 0
            while True:
                cursor, keys = await redis.scan(cursor=cursor, match=pattern, count=50)
                if keys:
                    deleted += await redis.delete(*keys)
                if cursor == 0:
                    break
            logger.info("OffsetManager: reset %d keys for %s/%s", deleted, consumer_group, topic)
            return deleted
        except Exception as e:
            logger.warning("OffsetManager: reset_offsets failed: %s", e)
            self._connection.mark_unavailable()
            return 0

    async def close(self) -> None:
        await self._connection.close()


_offset_manager: OffsetManager | None = None


def get_offset_manager() -> OffsetManager:
    global _offset_manager
    if _offset_manager is None:
        _offset_manager = OffsetManager()
    return _offset_manager
