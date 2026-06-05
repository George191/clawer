"""代理池管理 - Proxy Pool Management.

通用代理池框架，通过适配器接口与具体代理源解耦。

核心职责：
- 代理获取与调度（轮询/随机）
- 健康检查与失效剔除
- 故障计数与自动恢复
- 多代理源合并

代理源的加载与解析委托给各自的 ProxySourceAdapter 实现。
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Optional

from curl_cffi import requests as curl_requests

from app.anti_crawl.adapters.base import ProxyInfo, ProxySourceAdapter
from app.config.settings import settings

logger = logging.getLogger(__name__)


class ProxyPool:
    """通用代理池框架。

    通过适配器接口与具体代理源交互，实现代理获取、调度、健康检查、
    故障摘除与恢复等核心功能。不直接依赖任何特定的代理源实现。

    使用示例::

        from app.anti_crawl.adapters import ZdopenAPIAdapter

        pool = ProxyPool()
        pool.register_adapter(ZdopenAPIAdapter({
            "app_id": "xxx",
            "akey": "yyy",
        }))
        proxy = await pool.get_proxy()
    """

    def __init__(self) -> None:
        self._proxies: list[ProxyInfo] = []
        self._index: int = 0
        self._lock: asyncio.Lock = asyncio.Lock()
        self._healthy: list[ProxyInfo] = []
        self._unhealthy: list[ProxyInfo] = []
        self._load_lock: asyncio.Lock = asyncio.Lock()
        self._loaded: bool = False

        # 粘性代理（除非失败否则不换）
        self._current_proxy: ProxyInfo | None = None

        # 适配器注册表
        self._adapters: list[ProxySourceAdapter] = []

    # ── 适配器管理 ──────────────────────────────────────────────────────────

    def register_adapter(self, adapter: ProxySourceAdapter) -> None:
        """注册一个代理源适配器。

        支持注册多个适配器，代理池将合并所有适配器的代理。

        Args:
            adapter: 实现了 ProxySourceAdapter 接口的适配器实例。

        Raises:
            ValueError: 如果适配器配置无效。
        """
        if not adapter.validate_config():
            raise ValueError(
                f"Adapter '{adapter.name}' has invalid config: {adapter._config}"
            )
        self._adapters.append(adapter)
        self._loaded = False  # 重置加载状态
        logger.info("Registered proxy adapter: %s", adapter.name)

    def unregister_adapter(self, adapter_name: str) -> None:
        """注销指定名称的适配器。

        Args:
            adapter_name: 适配器名称。
        """
        self._adapters = [a for a in self._adapters if a.name != adapter_name]
        self._loaded = False
        logger.info("Unregistered proxy adapter: %s", adapter_name)

    @property
    def enabled(self) -> bool:
        """代理池是否已启用且有注册的适配器。"""
        return settings.anti_crawl_enabled and len(self._adapters) > 0

    @property
    def pool_size(self) -> int:
        return len(self._proxies)

    @property
    def healthy_count(self) -> int:
        return len(self._healthy)

    # ── 代理加载 ────────────────────────────────────────────────────────────

    async def ensure_loaded(self) -> None:
        """确保代理池已加载（懒加载，只加载一次）。"""
        if not self.enabled or self._loaded:
            return
        async with self._load_lock:
            if self._loaded:
                return
            await self._load_from_adapters()
            self._loaded = True

    async def _load_from_adapters(self) -> None:
        """从所有注册的适配器加载代理列表。"""
        if not self._adapters:
            logger.warning("No proxy adapters registered")
            return

        # 并行从所有适配器获取代理
        tasks = [adapter.fetch() for adapter in self._adapters]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for adapter, result in zip(self._adapters, results):
            if isinstance(result, Exception):
                logger.error("Adapter '%s' fetch failed: %s", adapter.name, result)
                continue
            for proxy_info in result:
                self._proxies.append(proxy_info)

        if not self._proxies:
            logger.warning("Proxy pool is empty after loading from all adapters")
            return

        # 去重：基于 URL 去重
        seen: set[str] = set()
        unique: list[ProxyInfo] = []
        for p in self._proxies:
            if p.url not in seen:
                seen.add(p.url)
                unique.append(p)
        self._proxies = unique
        
        # 过滤掉非HTTP/HTTPS的代理（排除socket相关协议）
        self._proxies = [p for p in self._proxies if p.url.startswith('http://') or p.url.startswith('https://')]
        
        if not self._proxies:
            logger.warning("Proxy pool is empty after filtering non-HTTP proxies")
            return

        # 初始分类：全部标记为健康
        self._healthy = list(self._proxies)
        self._unhealthy = []

        # 异步健康检查
        if self._healthy:
            check_tasks = [self._health_check(p) for p in self._proxies]
            await asyncio.gather(*check_tasks, return_exceptions=True)

        logger.info(
            "Proxy pool loaded: %d total, %d healthy, %d unhealthy",
            len(self._proxies),
            len(self._healthy),
            len(self._unhealthy),
        )

    async def reload(self) -> None:
        """强制重新加载代理池（清空现有代理并从适配器重新加载）。"""
        async with self._load_lock:
            self._proxies.clear()
            self._healthy.clear()
            self._unhealthy.clear()
            self._index = 0
            self._loaded = False
            await self._load_from_adapters()
            self._loaded = True

    # ── 健康检查 ────────────────────────────────────────────────────────────

    async def _health_check(self, proxy: ProxyInfo) -> bool:
        """对单个代理执行健康检查。

        使用 curl_cffi 与实际请求库保持一致性，这样可以更准确地检测代理在实际使用中的健康状况。

        Args:
            proxy: 待检查的代理信息。

        Returns:
            True 如果代理健康，否则 False。
        """
        health_url = settings.proxy_health_check_url or "https://httpbin.org/ip"
        try:
            async with curl_requests.AsyncSession(
                proxy=proxy.url,
                impersonate="chrome120",
                timeout=5.0,
                verify=settings.http_verify_ssl,
            ) as session:
                response = await session.get(health_url)
                if response.status_code < 500:
                    proxy.healthy = True
                    proxy.last_check = time.time()
                    logger.debug("Proxy health check passed: %s", proxy.url)
                    return True
        except Exception as e:
            logger.debug("Proxy health check failed for %s: %s", proxy.url, str(e))

        proxy.healthy = False
        proxy.last_check = time.time()
        return False

    # ── 代理获取 ────────────────────────────────────────────────────────────

    async def get_proxy(self) -> Optional[str]:
        """获取下一个可用代理。

        支持两种调度策略：
        - round_robin: 轮询（默认）
        - random: 随机
        
        策略：
        - 优先使用当前粘性代理（除非不健康）
        - 如果没有健康代理或代理池为空，尝试重新加载一次

        Returns:
            代理 URL 字符串，如果无可用代理则返回 None。
        """
        if not self.enabled:
            return None

        await self.ensure_loaded()

        # 1. 尝试使用粘性代理（如果健康）
        if self._current_proxy and self._current_proxy in self._healthy:
            logger.debug("Reusing sticky proxy: %s", self._current_proxy.url)
            return self._current_proxy.url

        # 2. 没有健康代理或代理池为空时，尝试重新加载
        if not self._healthy or not self._proxies:
            logger.warning("No healthy proxies or proxy pool empty, reloading...")
            self._loaded = False  # 强制重新加载
            await self.ensure_loaded()
            if not self._healthy:
                logger.warning("Still no healthy proxies after reload")
                return None

        # 3. 获取新代理并设置为粘性代理
        async with self._lock:
            if not self._healthy:
                return None

            strategy = settings.proxy_rotation.lower()
            if strategy == "random":
                proxy = random.choice(self._healthy)
            else:
                proxy = self._healthy[self._index % len(self._healthy)]
                self._index = (self._index + 1) % len(self._healthy)

            proxy.last_used = time.time()
            self._current_proxy = proxy  # 设置为当前粘性代理
            logger.debug("New sticky proxy: %s", proxy.url)
            return proxy.url

    # ── 故障管理 ────────────────────────────────────────────────────────────

    async def mark_failure(self, proxy_url: str) -> None:
        """标记代理请求失败，直接从代理池中移除。

        Args:
            proxy_url: 失败的代理 URL。
        """
        if not self.enabled:
            return

        async with self._lock:
            # 如果这是当前粘性代理，清除粘性
            if self._current_proxy and self._current_proxy.url == proxy_url:
                logger.debug("Clearing sticky proxy due to failure: %s", proxy_url)
                self._current_proxy = None
            
            # 从所有列表中移除该代理
            removed = False
            for proxy_list in [self._proxies, self._healthy, self._unhealthy]:
                to_remove = [p for p in proxy_list if p.url == proxy_url]
                if to_remove:
                    for p in to_remove:
                        proxy_list.remove(p)
                    removed = True
            
            if removed:
                logger.warning("Proxy removed from pool after failure: %s", proxy_url)

    async def mark_success(self, proxy_url: str) -> None:
        """标记代理请求成功，重置失败计数。

        Args:
            proxy_url: 成功的代理 URL。
        """
        if not self.enabled:
            return

        async with self._lock:
            for proxy in self._proxies:
                if proxy.url == proxy_url:
                    proxy.failures = 0
                    break

    # ── 恢复检查 ────────────────────────────────────────────────────────────

    async def periodic_health_recheck(self) -> None:
        """定期重新检查不健康代理，尝试恢复。"""
        if not self.enabled:
            return

        await self.ensure_loaded()

        if not self._unhealthy:
            return

        to_recheck = list(self._unhealthy)
        logger.debug("Re-checking %d unhealthy proxies", len(to_recheck))

        for proxy in to_recheck:
            if await self._health_check(proxy):
                async with self._lock:
                    if proxy in self._unhealthy:
                        self._unhealthy.remove(proxy)
                        self._healthy.append(proxy)
                        logger.info("Proxy recovered: %s", proxy.url)

    # ── 状态查询 ────────────────────────────────────────────────────────────

    def status(self) -> dict:
        """返回代理池状态快照。

        Returns:
            包含代理池各项统计信息的字典。
        """
        return {
            "total": len(self._proxies),
            "healthy": len(self._healthy),
            "unhealthy": len(self._unhealthy),
            "enabled": self.enabled,
            "adapters": [a.name for a in self._adapters],
            "healthy_proxies": [p.url for p in self._healthy],
        }


# ── 全局单例 ────────────────────────────────────────────────────────────────

_proxy_pool: Optional[ProxyPool] = None


def get_proxy_pool() -> ProxyPool:
    """获取全局代理池单例。

    Returns:
        全局唯一的 ProxyPool 实例。
    """
    global _proxy_pool
    if _proxy_pool is None:
        _proxy_pool = ProxyPool()
    
    adapters = _build_adapters_from_config()
    for adapter in adapters:
        _proxy_pool.register_adapter(adapter)
    return _proxy_pool


def _build_adapters_from_config() -> list[ProxySourceAdapter]:
    """从配置构建适配器列表。

    优先级：
    1. SPIDER_PROXY_SOURCES (新多源配置)
    2. SPIDER_PROXY_POOL_API_URL (旧单源 API 配置)
    3. SPIDER_PROXY_POOL_FILE (旧文件配置)

    Returns:
        适配器实例列表。
    """
    from app.anti_crawl.adapters import FileProxySourceAdapter, ZdopenAPIAdapter

    adapters: list[ProxySourceAdapter] = []

    if settings.proxy_pool_api_url:
        adapters.append(ZdopenAPIAdapter({"url": settings.proxy_pool_api_url}))
    elif settings.proxy_pool_file:
        adapters.append(FileProxySourceAdapter({"file_path": settings.proxy_pool_file}))

    return adapters