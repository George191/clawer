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

# ── 可重试的 curl 错误码 ──────────────────────────────────────────────────
# curl: (28) = 连接/操作超时
# curl: (7)  = 连接失败
# curl: (6)  = DNS 解析失败
_RETRYABLE_CURL_CODES = ("(28)", "(7)", "(6)")

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

    async def on_before_crawl(self, template: Any) -> None:
        """采集开始前：记录 NAVAREA 编号。"""
        navarea = getattr(template, "params", {})
        if hasattr(navarea, "get"):
            self._current_navarea = int(navarea.get("navarea_id", 1))
        logger.info(
            "[SealagomAdapter] Starting crawl for NAVAREA %d",
            self._current_navarea,
        )

    async def on_before_page(self, page: int, is_first: bool) -> None:
        """请求每页前：添加限速延迟。"""
        if not is_first:
            await asyncio.sleep(self._delay)
            logger.debug(
                "[SealagomAdapter] Delayed %.1fs before page %d",
                self._delay,
                page,
            )

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """每页数据返回后：清洗和补充字段。

        - 为每条记录补充 navarea_id
        - 过滤空记录
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
        # 超过最大重试次数，放弃当前页
        if attempt >= _MAX_RETRIES:
            logger.error(
                "[SealagomAdapter] NAVAREA %d page %d gave up after %d attempts: %s",
                self._current_navarea, page, attempt, error,
            )
            return "skip"

        error_str = str(error)

        # ── 404: 该区域不存在，直接跳过 ──
        if "404" in error_str:
            logger.info(
                "[SealagomAdapter] NAVAREA %d page %d returned 404, skipping",
                self._current_navarea, page,
            )
            return "skip"

        # ── 429 / 503: 限流或临时不可用 ──
        if "429" in error_str or "503" in error_str:
            wait = min(60, 10 * (attempt + 1))
            logger.warning(
                "[SealagomAdapter] Rate limited on page %d (attempt %d/%d), "
                "waiting %ds",
                page, attempt + 1, _MAX_RETRIES, wait,
            )
            await asyncio.sleep(wait)
            return None  # 继续重试

        # ── curl 连接超时 / 网络错误: 换代理 → 等待 → 重试 ──
        is_curl_timeout = any(code in error_str for code in _RETRYABLE_CURL_CODES)

        if is_curl_timeout:
            # 1) 尝试释放失败代理并获取新代理
            if self._client is not None:
                try:
                    await self._client.mark_last_proxy_failed()
                    logger.info(
                        "[SealagomAdapter] Released failed proxy, will retry with new proxy "
                        "on page %d (attempt %d/%d)",
                        page, attempt + 1, _MAX_RETRIES,
                    )
                except Exception:
                    pass  # 没有代理池时会静默失败

            # 2) 指数退避 + 随机抖动，避免雷群效应
            base_wait = min(60, 10 * (2 ** attempt))
            jitter = random.uniform(0, base_wait * 0.3)
            wait = base_wait + jitter

            logger.warning(
                "[SealagomAdapter] Connection error on page %d (attempt %d/%d): %s. "
                "Waiting %.1fs before retry...",
                page, attempt + 1, _MAX_RETRIES,
                error_str[:120], wait,
            )
            await asyncio.sleep(wait)
            return None  # 继续重试

        # ── 其他未知错误: 短暂等待后重试 ──
        wait = min(30, 5 * (attempt + 1))
        logger.warning(
            "[SealagomAdapter] Unknown error on page %d (attempt %d/%d), "
            "waiting %ds: %s",
            page, attempt + 1, _MAX_RETRIES, wait,
            error_str[:200],
        )
        await asyncio.sleep(wait)
        return None  # 继续重试
