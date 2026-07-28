"""Redis-backed checkpoints for active list-file crawl batches."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.base.redis_connection import RedisConnection
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
    def identity(self) -> str:
        return f"{self.start_line}:{self.end_line}"


@dataclass(frozen=True)
class CrawlRunState:
    start_line: int
    limit: int | None
    batch_size: int
    last_assigned_line: int


class PageCheckpointStore:
    """Persist the current page for one stable crawl identity."""

    def __init__(
        self,
        namespace: str,
        identity: str,
        task_id: str | None = None,
        redis_client: Any = None,
    ) -> None:
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        self.task_id = task_id
        self.namespace = namespace
        self.key = (
            f"crawler:checkpoint:{task_id}"
            if task_id
            else f"crawler:checkpoint:{namespace}:{digest}"
        )
        self._connection = RedisConnection(settings.redis_url, client=redis_client)

    async def connect(self) -> bool:
        return await self._connection.ensure_connected() is not None

    async def load(self) -> int | None:
        redis = await self._connection.ensure_connected()
        if redis is None:
            return None
        try:
            raw = await redis.hget(self.key, "page")
            if raw is None:
                return None
            page = int(raw)
            return page if page >= 0 else None
        except (TypeError, ValueError):
            logger.warning("忽略无效 Redis 页断点: key=%s", self.key)
            return None
        except Exception as exc:
            logger.warning("读取 Redis 页断点失败: %s", exc)
            self._connection.mark_unavailable()
            return None

    async def save(self, page: int) -> None:
        redis = await self._connection.ensure_connected()
        if redis is None:
            return
        try:
            pipeline = redis.pipeline(transaction=True)
            pipeline.hset(self.key, "page", str(page))
            if self.task_id:
                pipeline.hset(self.key, "task_id", self.task_id)
                pipeline.hset(self.key, "template", self.namespace)
            else:
                pipeline.expire(self.key, CHECKPOINT_TTL_SECONDS)
            await pipeline.execute()
        except Exception as exc:
            logger.warning("写入 Redis 页断点失败: %s", exc)
            self._connection.mark_unavailable()

    async def load_watermark(self) -> str | None:
        redis = await self._connection.ensure_connected()
        if redis is None:
            return None
        try:
            raw = await redis.hget(self.key, "watermark")
            return str(raw) if raw else None
        except Exception as exc:
            logger.warning("读取 Redis 水位失败: %s", exc)
            self._connection.mark_unavailable()
            return None

    async def complete(self, watermark: str | None) -> bool:
        """Remove active recovery fields and retain only a successful watermark."""
        redis = await self._connection.ensure_connected()
        if redis is None:
            return False
        try:
            if not watermark:
                await redis.delete(self.key)
                return True
            pipeline = redis.pipeline(transaction=True)
            pipeline.hdel(self.key, "page")
            pipeline.hset(self.key, "watermark", watermark)
            if self.task_id:
                pipeline.hset(self.key, "task_id", self.task_id)
                pipeline.hset(self.key, "template", self.namespace)
            else:
                pipeline.expire(self.key, CHECKPOINT_TTL_SECONDS)
            await pipeline.execute()
            return True
        except Exception as exc:
            logger.warning("提交 Redis 水位失败: %s", exc)
            self._connection.mark_unavailable()
            return False

    async def clear(self) -> bool:
        redis = await self._connection.ensure_connected()
        if redis is None:
            return False
        try:
            await redis.delete(self.key)
            return True
        except Exception as exc:
            logger.warning("删除 Redis 页断点失败: %s", exc)
            self._connection.mark_unavailable()
            return False

    async def close(self) -> None:
        await self._connection.close()


class BatchCheckpointStore:
    """Keep only currently running batch tasks in one Redis hash."""

    def __init__(
        self,
        template_name: str,
        file_path: str,
        param_name: str,
        start_line: int | None,
        limit: int | None,
        redis_client: Any = None,
    ) -> None:
        self.key = self._checkpoint_key(
            template_name, file_path, param_name, start_line, limit
        )
        self._connection = RedisConnection(settings.redis_url, client=redis_client)

    async def _get_redis(self) -> Any | None:
        return await self._connection.ensure_connected()

    @staticmethod
    def _checkpoint_key(
        template_name: str,
        file_path: str,
        param_name: str,
        start_line: int | None = None,
        limit: int | None = None,
    ) -> str:
        identity = "|".join((
            template_name,
            str(Path(file_path).resolve()),
            param_name,
        ))
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        return f"crawler:checkpoint:{template_name}:{digest}"

    async def load_run_state(self) -> CrawlRunState | None:
        redis = await self._get_redis()
        if redis is None:
            return None
        try:
            raw = await redis.hget(self.key, "run")
            if not raw:
                return None
            data = json.loads(raw)
            state = CrawlRunState(
                start_line=int(data["start_line"]),
                limit=int(data["limit"]) if data.get("limit") is not None else None,
                batch_size=int(data["batch_size"]),
                last_assigned_line=int(
                    data.get("last_assigned_line", data["start_line"] - 1)
                ),
            )
            return state
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("忽略无效 Redis 运行元数据: %s", exc)
            return None

    async def save_run_state(self, state: CrawlRunState) -> None:
        redis = await self._get_redis()
        if redis is None:
            return
        try:
            pipeline = redis.pipeline(transaction=True)
            pipeline.hset(self.key, "run", json.dumps(asdict(state)))
            pipeline.expire(self.key, CHECKPOINT_TTL_SECONDS)
            await pipeline.execute()
        except Exception as exc:
            logger.warning("写入 Redis 运行元数据失败: %s", exc)
            self._connection.mark_unavailable()

    async def connect(self) -> bool:
        if not settings.redis_url and self._connection.client is None:
            logger.info("Redis 未配置，断点续传缓存已禁用")
        return await self._get_redis() is not None

    async def load(self) -> list[BatchCheckpoint]:
        redis = await self._get_redis()
        if redis is None or self.key is None:
            return []
        try:
            raw_items = await redis.hgetall(self.key)
        except Exception as exc:
            logger.warning("读取 Redis checkpoint 失败: %s", exc)
            self._connection.mark_unavailable()
            return []
        return self._decode_checkpoints(
            raw for field, raw in raw_items.items() if field != "run"
        )

    @staticmethod
    def _decode_checkpoints(raw_values: Any) -> list[BatchCheckpoint]:
        checkpoints: list[BatchCheckpoint] = []
        for raw in raw_values:
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
        redis = await self._get_redis()
        if redis is None or self.key is None:
            return
        try:
            pipeline = redis.pipeline(transaction=True)
            pipeline.hset(self.key, checkpoint.identity, json.dumps(asdict(checkpoint)))
            pipeline.expire(self.key, CHECKPOINT_TTL_SECONDS)
            await pipeline.execute()
        except Exception as exc:
            logger.warning("写入 Redis checkpoint 失败: %s", exc)
            self._connection.mark_unavailable()

    async def delete(self, checkpoint_identity: str) -> None:
        redis = await self._get_redis()
        if redis is not None and self.key is not None:
            try:
                await redis.hdel(self.key, checkpoint_identity)
            except Exception as exc:
                logger.warning("删除 Redis checkpoint 失败: %s", exc)
                self._connection.mark_unavailable()

    async def clear(self) -> None:
        redis = await self._get_redis()
        if redis is not None:
            try:
                await redis.delete(self.key)
            except Exception as exc:
                logger.warning("清理 Redis checkpoint 失败: %s", exc)
                self._connection.mark_unavailable()

    async def close(self) -> None:
        await self._connection.close()
