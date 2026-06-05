"""Redis 去重与布隆过滤器 - URL Deduplication & Bloom Filter.

基于 Redis 的 URL 去重，支持增量采集模式。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from app.config.settings import settings

logger = logging.getLogger(__name__)


class RedisDedup:
    """Redis 去重管理器。

    特性：
    - 基于 Redis SET 的精确去重
    - 可选的布隆过滤器（Bloom Filter）减少内存占用
    - 增量采集模式：只采集新增/变更项
    - 支持自定义过期时间
    """

    def __init__(self) -> None:
        self._redis = None
        self._connected: bool = False
        self._bloom_filter: Optional[_BloomFilter] = None

    @property
    def enabled(self) -> bool:
        return settings.dedup_enabled and bool(settings.redis_url)

    async def _ensure_connected(self) -> None:
        """懒连接 Redis。"""
        if self._connected:
            return

        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            # 测试连接
            await self._redis.ping()
            self._connected = True

            if settings.bloom_filter_enabled:
                self._bloom_filter = _BloomFilter(
                    self._redis,
                    capacity=settings.bloom_filter_capacity,
                    error_rate=settings.bloom_filter_error_rate,
                    key_prefix=settings.bloom_filter_key_prefix,
                )

            logger.info(
                "Redis connected: %s, bloom_filter=%s",
                settings.redis_url,
                settings.bloom_filter_enabled,
            )

        except ImportError:
            logger.warning("redis package not installed, dedup disabled. Run: pip install redis")
            self._connected = False
        except Exception as e:
            logger.error("Failed to connect to Redis: %s", e)
            self._connected = False

    def _make_key(self, template_name: str, unique_id: str) -> str:
        """生成去重用的 Redis key。"""
        prefix = settings.dedup_key_prefix or "spider:dedup"
        return f"{prefix}:{template_name}:{unique_id}"

    async def exists(self, template_name: str, unique_id: str) -> bool:
        """检查 URL/记录是否已经采集过。

        Args:
            template_name: 模板名称
            unique_id: 唯一标识（如 URL hash、patent_id 等）

        Returns:
            True 如果已经存在（应跳过）
        """
        if not self.enabled:
            return False

        await self._ensure_connected()
        if not self._connected:
            return False

        key = self._make_key(template_name, unique_id)

        # 如果启用了布隆过滤器，先用布隆过滤器快速判断
        if self._bloom_filter is not None:
            if not await self._bloom_filter.might_contain(key):
                return False

        # 精确检查
        try:
            result = await self._redis.sismember(
                f"{settings.dedup_key_prefix or spider:dedup}:{template_name}:set",
                unique_id,
            )
            return bool(result)
        except Exception as e:
            logger.error("Redis exists check failed: %s", e)
            return False

    async def mark_seen(self, template_name: str, unique_id: str, ttl: Optional[int] = None) -> bool:
        """标记 URL/记录为已采集。

        Args:
            template_name: 模板名称
            unique_id: 唯一标识
            ttl: 过期时间（秒），None 表示永不过期

        Returns:
            True 如果新增成功
        """
        if not self.enabled:
            return True

        await self._ensure_connected()
        if not self._connected:
            return True  # 连接失败时允许继续采集

        try:
            set_key = f"{settings.dedup_key_prefix or spider:dedup}:{template_name}:set"
            added = await self._redis.sadd(set_key, unique_id)

            if ttl and added:
                await self._redis.expire(set_key, ttl)

            # 布隆过滤器标记
            if self._bloom_filter is not None:
                key = self._make_key(template_name, unique_id)
                await self._bloom_filter.add(key)

            return bool(added)
        except Exception as e:
            logger.error("Redis mark_seen failed: %s", e)
            return True  # 失败不阻塞采集

    async def get_seen_count(self, template_name: str) -> int:
        """获取已采集记录数。"""
        if not self.enabled:
            return 0

        await self._ensure_connected()
        if not self._connected:
            return 0

        try:
            set_key = f"{settings.dedup_key_prefix or spider:dedup}:{template_name}:set"
            return await self._redis.scard(set_key) or 0
        except Exception as e:
            logger.error("Redis scard failed: %s", e)
            return 0

    async def clear_template(self, template_name: str) -> bool:
        """清除指定模板的所有去重记录（重置采集状态）。"""
        if not self.enabled:
            return True

        await self._ensure_connected()
        if not self._connected:
            return False

        try:
            set_key = f"{settings.dedup_key_prefix or spider:dedup}:{template_name}:set"
            await self._redis.delete(set_key)
            logger.info("Cleared dedup set for template: %s", template_name)
            return True
        except Exception as e:
            logger.error("Redis delete failed: %s", e)
            return False

    async def record_digest(
        self,
        template_name: str,
        unique_id: str,
        content_hash: str,
    ) -> Optional[str]:
        """记录内容哈希，用于检测内容变更（增量采集模式）。

        Args:
            template_name: 模板名称
            unique_id: 唯一标识
            content_hash: 内容哈希值（如 SHA256）

        Returns:
            - None: 首次采集或未启用
            - content_hash: 内容未变更（与上次相同）
            - "changed": 内容已变更，需要重新采集
        """
        if not self.enabled:
            return None

        await self._ensure_connected()
        if not self._connected:
            return None

        try:
            hash_key = f"{settings.dedup_key_prefix or spider:dedup}:{template_name}:hash:{unique_id}"
            old_hash = await self._redis.get(hash_key)

            if old_hash is None:
                # 首次采集
                await self._redis.set(hash_key, content_hash)
                return None

            if old_hash == content_hash:
                logger.debug("Content unchanged for %s/%s, skipping", template_name, unique_id)
                return content_hash

            # 内容已变更
            await self._redis.set(hash_key, content_hash)
            return "changed"
        except Exception as e:
            logger.error("Redis content digest failed: %s", e)
            return None

    @staticmethod
    def make_content_hash(data: dict) -> str:
        """生成内容的 hash 值（SHA256）。"""
        import json
        # 按键排序确保一致性
        canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def close(self) -> None:
        """关闭 Redis 连接。"""
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._connected = False


class _BloomFilter:
    """基于 Redis 位图的布隆过滤器实现。

    参考经典的 Bloom Filter 算法，使用 Redis SETBIT/GETBIT 操作。
    具有恒定的空间复杂度和可配置的误判率。
    """

    def __init__(
        self,
        redis_client,
        capacity: int = 1000000,
        error_rate: float = 0.001,
        key_prefix: str = "spider:bloom",
    ) -> None:
        self._redis = redis_client
        self._key_prefix = key_prefix

        # 计算最优参数
        self._bit_size, self._hash_count = self._optimal_params(capacity, error_rate)
        logger.info(
            "Bloom filter initialized: capacity=%d, error_rate=%.4f, bits=%d, hashes=%d",
            capacity,
            error_rate,
            self._bit_size,
            self._hash_count,
        )

    @staticmethod
    def _optimal_params(n: int, p: float) -> tuple[int, int]:
        """计算布隆过滤器的最优参数。

        Args:
            n: 预期元素数量 (capacity)
            p: 期望误判率 (error_rate)

        Returns:
            (bit_size, hash_count) 最优位数组大小和哈希函数数量
        """
        import math

        # m = -(n * ln(p)) / (ln(2)^2)
        m = int(-(n * math.log(p)) / (math.log(2) ** 2))

        # k = (m / n) * ln(2)
        k = int((m / n) * math.log(2))

        # 至少1个哈希函数，至少1位
        return max(1, m), max(1, k)

    def _get_offsets(self, key: str) -> list[int]:
        """使用多个哈希函数生成位偏移量（double hashing 技术）。"""
        h1 = int(hashlib.md5(f"{key}:h1".encode()).hexdigest(), 16)
        h2 = int(hashlib.sha1(f"{key}:h2".encode()).hexdigest(), 16)

        offsets = []
        for i in range(self._hash_count):
            offset = (h1 + i * h2) % self._bit_size
            offsets.append(offset)

        return offsets

    async def add(self, key: str) -> None:
        """向布隆过滤器添加一个元素。"""
        bloom_key = f"{self._key_prefix}:bits"
        offsets = self._get_offsets(key)

        pipe = self._redis.pipeline()
        for offset in offsets:
            pipe.setbit(bloom_key, offset, 1)
        await pipe.execute()

    async def might_contain(self, key: str) -> bool:
        """检查元素是否可能存在（可能误判为存在，但不会漏判）。"""
        bloom_key = f"{self._key_prefix}:bits"
        offsets = self._get_offsets(key)

        pipe = self._redis.pipeline()
        for offset in offsets:
            pipe.getbit(bloom_key, offset)
        results = await pipe.execute()

        return all(results)


# 全局单例
_dedup: Optional[RedisDedup] = None


def get_dedup() -> RedisDedup:
    """获取全局去重管理器单例。"""
    global _dedup
    if _dedup is None:
        _dedup = RedisDedup()
    return _dedup
