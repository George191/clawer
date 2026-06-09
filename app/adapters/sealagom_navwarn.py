"""Sealagom NAVAREA 适配器 — 处理航行警告消息采集。

核心逻辑
--------
Sealagom (www.sealagom.com) 提供全球 NAVAREA I~XX 的航行警告列表。
页面为服务端渲染 HTML，无需特殊信令。

URL 规则：
- 列表页: /navarea/{n}/messages/       (n = 1~20)
- 详情页: /navarea/{n}/message/{id}/{slug}/
- 归档:   /navarea/{n}/messages/archive/

本适配器处理：
1. 请求头伪装（模拟普通浏览器访问）
2. 请求限速（尊重站点的合理使用）
3. 按 NAVAREA 分区编号 (1-20) 循环采集
4. 连接超时 / 网络错误时自动换代理或等待重试
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

from app.adapters import BaseSiteAdapter, register_adapter
from app.downloader.http_client import HttpClient

logger = logging.getLogger(__name__)

# ── 可重试的网络错误特征 ───────────────────────────────────────────────────
# curl: (28) = 连接/操作超时
# curl: (7)  = 连接失败
# curl: (6)  = DNS 解析失败
# HTTP Error 0   = curl_cffi 连接被重置/中断（状态码为 0）
# HTTP Error 103 = Early Hints，curl_cffi 处理异常
_RETRYABLE_PATTERNS = ("(28)", "(7)", "(6)", "HTTP Error 0", "HTTP Error 103")

# 最大重试次数（超出后放弃当前页）
_MAX_RETRIES = 5


@register_adapter("sealagom")
class SealagomAdapter(BaseSiteAdapter):
    """Sealagom 航行警告站点适配器。"""

    adapter_name = "sealagom"

    # 默认请求间隔 (秒), 避免对站点造成压力
    DEFAULT_DELAY: float = 2.0

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._delay = kwargs.get("request_delay", self.DEFAULT_DELAY)
        self._current_navarea: int = 1
        self._retry_count: int = 0   # 本次采集累计重试次数
        self._error_count: int = 0   # 本次采集累计错误次数

    async def on_before_crawl(self, template: Any) -> None:
        """采集开始前：解析 batch_data 并记录 NAVAREA 编号。"""
        # 基类处理 _batch_data → 填入 navarea_id
        await super().on_before_crawl(template)

        # 从解析后的 param_values 读取 navarea_id
        param_values = getattr(template, "_param_values", {}) or {}
        navarea_id = param_values.get("navarea_id", "1")
        try:
            self._current_navarea = int(navarea_id)
        except (ValueError, TypeError):
            self._current_navarea = 1
        self._retry_count = 0
        self._error_count = 0
        logger.info(
            "[SealagomAdapter] ▶ Starting crawl for NAVAREA %d",
            self._current_navarea,
        )

    async def on_before_page(self, page: int, is_first: bool) -> None:
        """请求每页前：限速延迟。重试时按指数退避让开并发洪峰。"""
        if self._retry_count > 0:
            # 重试场景：指数退避 + 随机抖动，打散 20 个协程的同步重试
            base = min(30, 3 * (2 ** self._retry_count))
            jitter = random.uniform(0, base * 0.5)
            wait = base + jitter
            logger.debug(
                "[SealagomAdapter] Retry backoff: NAVAREA %d page %d, "
                "waiting %.1fs (retry #%d)",
                self._current_navarea, page, wait, self._retry_count,
            )
            await asyncio.sleep(wait)
        elif not is_first:
            await asyncio.sleep(self._delay)

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """每页数据返回后：清洗和补充字段。

        - 为每条记录补充 navarea_id
        - 过滤空记录
        - 重试成功后打印恢复日志
        """
        enriched = []
        for record in records:
            if not record.get("message_id") and not record.get("title"):
                continue
            record["navarea_id"] = self._current_navarea
            enriched.append(record)

        if len(enriched) < len(records):
            logger.info(
                "[SealagomAdapter] Filtered %d empty records on page %d",
                len(records) - len(enriched),
                page,
            )

        # 如果之前有过重试，打印恢复成功日志
        if self._retry_count > 0:
            logger.info(
                "[SealagomAdapter] ✓ NAVAREA %d page %d recovered after %d retries "
                "(total errors: %d), got %d records",
                self._current_navarea, page, self._retry_count,
                self._error_count, len(enriched),
            )
            self._retry_count = 0  # 重置，为下一页准备

        return enriched

    def on_request_headers(self, page: int) -> dict[str, str]:
        """注入请求头 — 模拟普通浏览器。"""
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "no-cache",
        }

    async def on_error(self, error: Exception, page: int, attempt: int) -> str | None:
        """处理 Sealagom 特有错误，带重试上限保护。

        策略：
        - 连接超时 / 网络错误 → 优先换代理，无代理则等待重试
        - 429 / 503          → 延长等待后重试
        - 404                → 该 NAVAREA 无数据，跳过
        - 超出最大重试次数    → 放弃当前页
        """
        self._error_count += 1
        navarea = self._current_navarea
        err_short = str(error)[:100]

        # 超过最大重试次数，放弃当前页
        if attempt >= _MAX_RETRIES:
            logger.error(
                "[SealagomAdapter] ✗ NAVAREA %d page %d GAVE UP after %d attempts "
                "(cumulative errors: %d). Last error: %s",
                navarea, page, attempt, self._error_count, err_short,
            )
            return "skip"

        error_str = str(error)

        # ── 404: 该区域不存在，直接跳过 ──
        if "404" in error_str:
            logger.info(
                "[SealagomAdapter] NAVAREA %d returned 404, skipping",
                navarea,
            )
            return "skip"

        # ── 429 / 503: 限流或临时不可用 ──
        if "429" in error_str or "503" in error_str:
            wait = min(60, 10 * (attempt + 1))
            self._retry_count += 1
            logger.warning(
                "[SealagomAdapter] ⏳ NAVAREA %d page %d RATE LIMITED "
                "[retry %d/%d, total errors: %d] — waiting %ds then retry",
                navarea, page, attempt + 1, _MAX_RETRIES,
                self._error_count, wait,
            )
            await asyncio.sleep(wait)
            return None

        # ── curl 超时 / HTTP Error 0 / 连接重置: 换代理 → 等待 → 重试 ──
        is_network_error = any(p in error_str for p in _RETRYABLE_PATTERNS)

        if is_network_error:
            # 1) 尝试释放失败代理并获取新代理
            proxy_switched = False
            if self._client is not None:
                try:
                    await self._client.mark_last_proxy_failed()
                    proxy_switched = True
                except Exception:
                    pass

            # 2) 指数退避 + 随机抖动，避免雷群效应
            base_wait = min(60, 10 * (2 ** attempt))
            jitter = random.uniform(0, base_wait * 0.3)
            wait = base_wait + jitter
            self._retry_count += 1

            proxy_status = "proxy switched" if proxy_switched else "no proxy pool, direct retry"
            logger.warning(
                "[SealagomAdapter] ⏳ NAVAREA %d page %d CONNECTION ERROR "
                "[retry %d/%d, total errors: %d, %s] — wait %.1fs then retry\n"
                "    └─ %s",
                navarea, page, attempt + 1, _MAX_RETRIES,
                self._error_count, proxy_status, wait,
                err_short,
            )
            await asyncio.sleep(wait)
            return None

        # ── 其他未知错误: 短暂等待后重试 ──
        wait = min(30, 5 * (attempt + 1))
        self._retry_count += 1
        logger.warning(
            "[SealagomAdapter] ⏳ NAVAREA %d page %d UNKNOWN ERROR "
            "[retry %d/%d, total errors: %d] — wait %ds then retry\n"
            "    └─ %s",
            navarea, page, attempt + 1, _MAX_RETRIES,
            self._error_count, wait, err_short,
        )
        await asyncio.sleep(wait)
        return None
