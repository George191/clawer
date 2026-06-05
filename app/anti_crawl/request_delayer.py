"""请求延迟策略 - Request Delay Strategy.

可配置的随机延迟范围和按域名限速，通过 asyncio 实现非阻塞延迟。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Optional
from urllib.parse import urlparse

from app.config.settings import settings

logger = logging.getLogger(__name__)


class RequestDelayer:
    """请求延迟控制器。

    特性：
    - 每次请求之间添加随机延迟（random.uniform(min, max)）
    - 按域名限速：对指定域名限制每秒请求数
    - 支持默认域名速率（对所有域名统一限速）
    - 基于令牌桶算法（token bucket）实现平滑限速
    """

    def __init__(self) -> None:
        # 域名级令牌桶: {domain: {"tokens": float, "last_refill": float, "rate": float, "capacity": float}}
        self._buckets: dict[str, dict] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return settings.anti_crawl_enabled and (
            settings.request_delay_min > 0
            or settings.request_delay_max > 0
            or bool(settings.domain_rate_limit)
        )

    @property
    def delay_min(self) -> float:
        return settings.request_delay_min

    @property
    def delay_max(self) -> float:
        return settings.request_delay_max

    async def delay(self, url: Optional[str] = None) -> None:
        """执行延迟等待。

        先执行随机延迟（通用延迟），再检查域名级限速。

        Args:
            url: 请求的 URL，用于按域名限速。为 None 时仅执行通用延迟。
        """
        if not self.enabled:
            return

        # 1. 随机延迟
        if self.delay_min > 0 or self.delay_max > 0:
            sleep_time = random.uniform(self.delay_min, max(self.delay_min, self.delay_max))
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

        # 2. 域名级限速
        if url:
            await self._domain_rate_limit(url)

    async def _domain_rate_limit(self, url: str) -> None:
        """按域名限制请求速率（令牌桶算法）。

        从 settings.domain_rate_limit 读取配置：
        {"patents.google.com": 5} → 该域名最多 5 req/s
        {"*": 3} → 所有域名默认 3 req/s

        示例配置：
            domain_rate_limit = {"patents.google.com": 5, "*.google.com": 3}
        """
        if not settings.domain_rate_limit:
            return

        domain = urlparse(url).hostname or ""
        if not domain:
            return

        rate = self._resolve_domain_rate(domain)
        if rate is None or rate <= 0:
            return

        now = time.monotonic()
        async with self._lock:
            bucket = self._buckets.get(domain)

            if bucket is None:
                # 初始化令牌桶：容量 = rate，满桶启动
                bucket = {
                    "tokens": float(rate),
                    "last_refill": now,
                    "rate": float(rate),
                    "capacity": float(rate),
                }
                self._buckets[domain] = bucket

            # 补充令牌
            elapsed = now - bucket["last_refill"]
            bucket["tokens"] = min(
                bucket["capacity"],
                bucket["tokens"] + elapsed * bucket["rate"],
            )
            bucket["last_refill"] = now

            # 消耗令牌
            if bucket["tokens"] >= 1.0:
                bucket["tokens"] -= 1.0
            else:
                # 需要等待：计算等待时间 (token 不足 1 个)
                wait_time = (1.0 - bucket["tokens"]) / bucket["rate"]
                bucket["tokens"] = 0.0
                # 释放锁后等待
                pass

        if "wait_time" in dir() and isinstance(wait_time, float) and wait_time > 0:
            logger.debug("Rate limiting %s: waiting %.2fs", domain, wait_time)
            await asyncio.sleep(wait_time)

            # 等待后重新消耗令牌
            now = time.monotonic()
            async with self._lock:
                bucket = self._buckets[domain]
                elapsed = now - bucket["last_refill"]
                bucket["tokens"] = min(
                    bucket["capacity"],
                    bucket["tokens"] + elapsed * bucket["rate"],
                )
                bucket["last_refill"] = now
                bucket["tokens"] -= 1.0

    def _resolve_domain_rate(self, domain: str) -> Optional[float]:
        """解析域名对应的速率限制。

        策略：精确匹配优先，然后通配符匹配（如 *.google.com），最后默认值 *。
        """
        rate_map = settings.domain_rate_limit

        # 精确匹配
        if domain in rate_map:
            return float(rate_map[domain])

        # 通配符匹配（从长到短尝试）
        for pattern, rate in sorted(rate_map.items(), key=lambda x: -len(x[0])):
            if pattern.startswith("*."):
                suffix = pattern[1:]  # ".google.com"
                if domain.endswith(suffix) or domain == pattern[2:]:
                    return float(rate)

        # 默认值
        if "*" in rate_map:
            return float(rate_map["*"])

        return None


# 全局单例
_delayer: Optional[RequestDelayer] = None


def get_delayer() -> RequestDelayer:
    """获取全局请求延迟器单例。"""
    global _delayer
    if _delayer is None:
        _delayer = RequestDelayer()
    return _delayer
