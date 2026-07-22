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
    def task_id(self) -> str:
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
        redis_client: Any = None,
    ) -> None:
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        self.key = f"crawler:checkpoint-page:{namespace}:{digest}"
        self._connection = RedisConnection(settings.redis_url, client=redis_client)

    async def connect(self) -> bool:
        return await self._connection.ensure_connected() is not None

    async def load(self) -> int | None:
        redis = await self._connection.ensure_connected()
        if redis is None:
            return None
        try:
            raw = await redis.get(self.key)
            if raw is None:
                return None
            page = int(raw)
            return page if page > 0 else None
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
            await redis.set(self.key, str(page), ex=CHECKPOINT_TTL_SECONDS)
        except Exception as exc:
            logger.warning("写入 Redis 页断点失败: %s", exc)
            self._connection.mark_unavailable()

    async def clear(self) -> None:
        redis = await self._connection.ensure_connected()
        if redis is None:
            return
        try:
            await redis.delete(self.key)
        except Exception as exc:
            logger.warning("删除 Redis 页断点失败: %s", exc)
            self._connection.mark_unavailable()

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
        self._template_name = template_name
        self._file_path = file_path
        self._param_name = param_name
        base_identity = "|".join(
            (template_name, str(Path(file_path).resolve()), param_name)
        )
        base_digest = hashlib.sha256(base_identity.encode("utf-8")).hexdigest()[:20]
        self.state_key = f"crawler:checkpoint-state:{template_name}:{base_digest}"
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
            raw = await redis.get(self.state_key)
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
            self.key = data["checkpoint_key"]
            return state
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("忽略无效 Redis 运行元数据: %s", exc)
            return None

    async def save_run_state(self, state: CrawlRunState) -> None:
        redis = await self._get_redis()
        if redis is None:
            return
        if self.key is None:
            self.key = self._checkpoint_key(
                self._template_name,
                self._file_path,
                self._param_name,
                state.start_line,
                state.limit,
            )
        payload = {
            **asdict(state),
            "checkpoint_key": self.key,
        }
        try:
            await redis.set(
                self.state_key,
                json.dumps(payload),
                ex=CHECKPOINT_TTL_SECONDS,
            )
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
        return self._decode_checkpoints(raw_items.values())

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

    async def discover_legacy_checkpoints(self) -> list[BatchCheckpoint]:
        """Find the most recently active legacy hash when no run metadata exists."""
        redis = await self._get_redis()
        if redis is None:
            return []
        pattern = f"crawler:checkpoint:{self._template_name}:*"
        candidates: list[tuple[int, str, list[BatchCheckpoint]]] = []
        try:
            async for key in redis.scan_iter(match=pattern):
                raw_items = await redis.hgetall(key)
                checkpoints = self._decode_checkpoints(raw_items.values())
                if not checkpoints:
                    continue
                ttl = await redis.ttl(key)
                candidates.append((int(ttl), key, checkpoints))
        except Exception as exc:
            logger.warning("自动发现旧 Redis checkpoint 失败: %s", exc)
            self._connection.mark_unavailable()
            return []

        if not candidates:
            return []
        _, key, checkpoints = max(
            candidates,
            key=lambda item: (item[0], max(cp.end_line for cp in item[2])),
        )
        self.key = key
        if len(candidates) > 1:
            logger.warning(
                "发现 %d 个旧 checkpoint hash，选择最近活动的 %s",
                len(candidates),
                key,
            )
        return checkpoints

    async def save(self, checkpoint: BatchCheckpoint) -> None:
        redis = await self._get_redis()
        if redis is None or self.key is None:
            return
        try:
            pipeline = redis.pipeline(transaction=True)
            pipeline.hset(self.key, checkpoint.task_id, json.dumps(asdict(checkpoint)))
            pipeline.expire(self.key, CHECKPOINT_TTL_SECONDS)
            pipeline.expire(self.state_key, CHECKPOINT_TTL_SECONDS)
            await pipeline.execute()
        except Exception as exc:
            logger.warning("写入 Redis checkpoint 失败: %s", exc)
            self._connection.mark_unavailable()

    async def delete(self, task_id: str) -> None:
        redis = await self._get_redis()
        if redis is not None and self.key is not None:
            try:
                await redis.hdel(self.key, task_id)
            except Exception as exc:
                logger.warning("删除 Redis checkpoint 失败: %s", exc)
                self._connection.mark_unavailable()

    async def clear(self) -> None:
        redis = await self._get_redis()
        if redis is not None:
            try:
                keys = [self.state_key]
                if self.key is not None:
                    keys.append(self.key)
                await redis.delete(*keys)
            except Exception as exc:
                logger.warning("清理 Redis checkpoint 失败: %s", exc)
                self._connection.mark_unavailable()

    async def close(self) -> None:
        await self._connection.close()
