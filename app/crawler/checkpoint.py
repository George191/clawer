"""Redis-backed checkpoints for active list-file crawl batches."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.config.settings import settings
from app.logger import get_logger

logger = get_logger(__name__)

CHECKPOINT_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class BatchCheckpoint:
    start_line: int
    end_line: int
    current_page: int

    @property
    def task_id(self) -> str:
        return f"{self.start_line}:{self.end_line}"


class BatchCheckpointStore:
    """Keep only currently running batch tasks in one Redis hash."""

    def __init__(
        self,
        template_name: str,
        file_path: str,
        param_name: str,
        start_line: int,
        limit: int | None,
        redis_client: Any = None,
    ) -> None:
        identity = "|".join(
            (
                template_name,
                str(Path(file_path).resolve()),
                param_name,
                str(start_line),
                str(limit),
            )
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        self.key = f"crawler:checkpoint:{template_name}:{digest}"
        self._redis = redis_client
        self._owns_client = redis_client is None

    async def connect(self) -> bool:
        if self._redis is not None:
            return True
        if not settings.redis_url:
            logger.info("Redis 未配置，断点续传缓存已禁用")
            return False
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=3,
            )
            await self._redis.ping()
            return True
        except Exception as exc:
            logger.warning("Redis checkpoint 连接失败，继续执行但不缓存进度: %s", exc)
            self._redis = None
            return False

    async def load(self) -> list[BatchCheckpoint]:
        if self._redis is None:
            return []
        try:
            raw_items = await self._redis.hgetall(self.key)
        except Exception as exc:
            logger.warning("读取 Redis checkpoint 失败: %s", exc)
            return []
        checkpoints: list[BatchCheckpoint] = []
        for raw in raw_items.values():
            try:
                data = json.loads(raw)
                checkpoints.append(BatchCheckpoint(
                    start_line=int(data["start_line"]),
                    end_line=int(data["end_line"]),
                    current_page=int(data["current_page"]),
                ))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                logger.warning("忽略无效 Redis checkpoint: %r", raw)
        return sorted(checkpoints, key=lambda item: item.start_line)

    async def save(self, checkpoint: BatchCheckpoint) -> None:
        if self._redis is None:
            return
        try:
            pipeline = self._redis.pipeline(transaction=True)
            pipeline.hset(self.key, checkpoint.task_id, json.dumps(asdict(checkpoint)))
            pipeline.expire(self.key, CHECKPOINT_TTL_SECONDS)
            await pipeline.execute()
        except Exception as exc:
            logger.warning("写入 Redis checkpoint 失败: %s", exc)

    async def delete(self, task_id: str) -> None:
        if self._redis is not None:
            try:
                await self._redis.hdel(self.key, task_id)
            except Exception as exc:
                logger.warning("删除 Redis checkpoint 失败: %s", exc)

    async def clear(self) -> None:
        if self._redis is not None:
            try:
                await self._redis.delete(self.key)
            except Exception as exc:
                logger.warning("清理 Redis checkpoint 失败: %s", exc)

    async def close(self) -> None:
        if self._redis is not None and self._owns_client:
            try:
                await self._redis.aclose()
            except Exception as exc:
                logger.warning("关闭 Redis checkpoint 连接失败: %s", exc)
        self._redis = None
