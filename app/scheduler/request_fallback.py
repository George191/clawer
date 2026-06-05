"""请求失败自动降级 - Request Fallback Strategy.

代理不可用时自动切换直连，并支持自动恢复。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from app.config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class FallbackState:
    """降级状态。"""

    mode: str = "proxy"  # proxy | direct
    failures: int = 0
    last_failure_time: float = 0.0
    last_success_time: float = 0.0
    direct_successes: int = 0
    proxy_retries: int = 0


class RequestFallback:
    """请求降级策略管理。

    当使用代理请求连续失败达到阈值，自动切换到直连模式；
    当直连模式稳定工作一段时间后，定期尝试恢复代理模式。

    策略参数（从 settings 读取）：
    - max_proxy_failures: 代理连续失败多少次后降级（默认 5 次）
    - cooldown_seconds: 直连稳定多久后尝试恢复代理（默认 300 秒）
    - recovery_retry_interval: 恢复尝试的间隔（默认 60 秒）
    """

    def __init__(self) -> None:
        # {domain: FallbackState}
        self._states: dict[str, FallbackState] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return settings.fallback_enabled

    def _get_domain(self, url: str) -> str:
        """从 URL 提取域名。"""
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.hostname or ""

    async def get_mode(self, url: str) -> str:
        """获取当前请求模式（proxy 或 direct）。

        Args:
            url: 请求的 URL

        Returns:
            "proxy" 或 "direct"
        """
        if not self.enabled:
            return "proxy"  # 禁用时始终尝试代理（如果已配置）

        domain = self._get_domain(url)
        async with self._lock:
            state = self._states.get(domain)
            if state is None:
                return "proxy"

            # 直连模式下检查是否可以尝试恢复
            if state.mode == "direct":
                now = time.monotonic()
                stable_time = now - state.last_success_time
                cooldown = settings.fallback_cooldown_seconds

                if stable_time >= cooldown:
                    if state.proxy_retries < 3:
                        state.proxy_retries += 1
                        state.mode = "proxy"
                        logger.info(
                            "Attempting proxy recovery for %s (retry %d)",
                            domain,
                            state.proxy_retries,
                        )
                        return "proxy"

            return state.mode

    async def record_success(self, url: str, mode: str) -> None:
        """记录请求成功。"""
        if not self.enabled:
            return

        domain = self._get_domain(url)
        async with self._lock:
            state = self._states.get(domain)
            if state is None:
                state = FallbackState()
                self._states[domain] = state

            now = time.monotonic()
            state.last_success_time = now

            if mode == "proxy":
                # 代理成功，重置失败计数
                state.failures = 0
                state.mode = "proxy"
                state.proxy_retries = 0
            elif mode == "direct":
                state.direct_successes += 1

            logger.debug(
                "Recorded success for %s (%s): failures=%d",
                domain,
                mode,
                state.failures,
            )

    async def record_failure(self, url: str, mode: str) -> str:
        """记录请求失败，返回建议的下一个模式。

        Args:
            url: 请求的 URL
            mode: 失败时的模式 ("proxy" 或 "direct")

        Returns:
            建议尝试的下一个模式
        """
        if not self.enabled:
            return mode

        domain = self._get_domain(url)
        async with self._lock:
            state = self._states.get(domain)
            if state is None:
                state = FallbackState()
                self._states[domain] = state

            now = time.monotonic()
            state.last_failure_time = now
            state.failures += 1

            if mode == "proxy":
                max_failures = settings.fallback_max_proxy_failures
                if state.failures >= max_failures:
                    state.mode = "direct"
                    state.failures = 0
                    state.direct_successes = 0
                    logger.warning(
                        "Proxy failed %d times for %s, switching to direct mode",
                        max_failures,
                        domain,
                    )
                    return "direct"
            elif mode == "direct":
                # 直连也失败，可能网络有问题
                state.failures += 1

            return mode

    async def force_direct(self, domain: str) -> None:
        """强制切换到直连模式。"""
        async with self._lock:
            state = self._states.get(domain)
            if state is None:
                state = FallbackState()
                self._states[domain] = state
            state.mode = "direct"
            state.failures = 0
            logger.info("Forced direct mode for %s", domain)

    async def status(self) -> dict:
        """返回所有域名的降级状态。"""
        async with self._lock:
            return {
                domain: {
                    "mode": state.mode,
                    "failures": state.failures,
                    "direct_successes": state.direct_successes,
                    "proxy_retries": state.proxy_retries,
                }
                for domain, state in self._states.items()
            }


# 全局单例
_fallback: Optional[RequestFallback] = None


def get_request_fallback() -> RequestFallback:
    """获取全局请求降级管理器单例。"""
    global _fallback
    if _fallback is None:
        _fallback = RequestFallback()
    return _fallback
