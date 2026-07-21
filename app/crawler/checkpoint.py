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


@dataclass(frozen=True)
class CrawlRunState:
    start_line: int
    limit: int | None
    batch_size: int
    last_assigned_line: int


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
        self._redis = redis_client
        self._owns_client = redis_client is None

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
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(self.state_key)
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
        if self._redis is None:
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
            await self._redis.set(
                self.state_key,
                json.dumps(payload),
                ex=CHECKPOINT_TTL_SECONDS,
            )
        except Exception as exc:
            logger.warning("写入 Redis 运行元数据失败: %s", exc)

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
        if self._redis is None or self.key is None:
            return []
        try:
            raw_items = await self._redis.hgetall(self.key)
        except Exception as exc:
            logger.warning("读取 Redis checkpoint 失败: %s", exc)
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
        if self._redis is None:
            return []
        pattern = f"crawler:checkpoint:{self._template_name}:*"
        candidates: list[tuple[int, str, list[BatchCheckpoint]]] = []
        try:
            async for key in self._redis.scan_iter(match=pattern):
                raw_items = await self._redis.hgetall(key)
                checkpoints = self._decode_checkpoints(raw_items.values())
                if not checkpoints:
                    continue
                ttl = await self._redis.ttl(key)
                candidates.append((int(ttl), key, checkpoints))
        except Exception as exc:
            logger.warning("自动发现旧 Redis checkpoint 失败: %s", exc)
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
        if self._redis is None or self.key is None:
            return
        try:
            pipeline = self._redis.pipeline(transaction=True)
            pipeline.hset(self.key, checkpoint.task_id, json.dumps(asdict(checkpoint)))
            pipeline.expire(self.key, CHECKPOINT_TTL_SECONDS)
            pipeline.expire(self.state_key, CHECKPOINT_TTL_SECONDS)
            await pipeline.execute()
        except Exception as exc:
            logger.warning("写入 Redis checkpoint 失败: %s", exc)

    async def delete(self, task_id: str) -> None:
        if self._redis is not None and self.key is not None:
            try:
                await self._redis.hdel(self.key, task_id)
            except Exception as exc:
                logger.warning("删除 Redis checkpoint 失败: %s", exc)

    async def clear(self) -> None:
        if self._redis is not None:
            try:
                keys = [self.state_key]
                if self.key is not None:
                    keys.append(self.key)
                await self._redis.delete(*keys)
            except Exception as exc:
                logger.warning("清理 Redis checkpoint 失败: %s", exc)

    async def close(self) -> None:
        if self._redis is not None and self._owns_client:
            try:
                await self._redis.aclose()
            except Exception as exc:
                logger.warning("关闭 Redis checkpoint 连接失败: %s", exc)
        self._redis = None
