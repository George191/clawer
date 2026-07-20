"""NGA 海事航行警告适配器 — 处理 NGA 站点的航行警告消息采集。

核心逻辑
--------
NGA (National Geospatial-Intelligence Agency) 的 NavWarnings 页面提供全球各区域的航行警告列表。
页面为服务器端渲染 HTML，结构清晰。

URL 规则：
- 列表页: /NavWarnings
- 详情页: /NavWarnings/<id>/<title>

本适配器处理：
1. 请求头伪装（模拟普通浏览器访问）
2. 请求限速（尊重站点的合理使用）
3. 数据清洗和标准化（统一区域命名、时间格式转换）
4. 连接超时 / 网络错误时自动换代理或等待重试
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from app.adapters import BaseSiteAdapter, register_adapter
from app.downloader.http_client import HttpClient
from app.logging_utils import get_adapter_logger

logger = get_adapter_logger(__name__, "nga")

# ── 可重试的网络错误特征 ───────────────────────────────────────────────────
_RETRYABLE_PATTERNS = ("(28)", "(7)", "(6)", "HTTP Error 0", "HTTP Error 103")

# 最大重试次数（超出后放弃当前页）
_MAX_RETRIES = 5


@register_adapter("nga_navwarn")
class NgaNavwarnAdapter(BaseSiteAdapter):
    """NGA 海事航行警告站点适配器。"""

    adapter_name = "nga_navwarn"

    # 默认请求间隔 (秒), 避免对站点造成压力
    DEFAULT_DELAY: float = 3.0

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._delay = kwargs.get("request_delay", self.DEFAULT_DELAY)
        self._retry_count: int = 0
        self._error_count: int = 0
        self._seen_warning_nos: set[str] = set()

    async def on_before_crawl(self, template: Any) -> None:
        """采集开始前：初始化状态。"""
        await super().on_before_crawl(template)
        self._retry_count = 0
        self._error_count = 0
        self._seen_warning_nos.clear()
        logger.info("[NgaNavwarnAdapter] ▶ Starting crawl for NGA NavWarnings")

    async def on_before_page(self, page: int, is_first: bool) -> None:
        """请求每页前：限速延迟。重试时按指数退避让开并发洪峰。"""
        if self._retry_count > 0:
            base = min(30, 3 * (2 ** self._retry_count))
            jitter = random.uniform(0, base * 0.5)
            wait = base + jitter
            logger.debug(
                "[NgaNavwarnAdapter] Retry backoff: page %d, "
                "waiting %.1fs (retry #%d)",
                page, wait, self._retry_count,
            )
            await asyncio.sleep(wait)
        elif not is_first:
            await asyncio.sleep(self._delay)

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """每页数据返回后：清洗和补充字段。

        - 为每条记录补充数据源标识
        - 标准化区域名称
        - 过滤空记录和重复记录
        """
        enriched = []
        empty_records = 0
        duplicate_records = 0

        for record in records:
            if not record.get("warning_no") and not record.get("subject"):
                empty_records += 1
                continue

            warning_no = str(record.get("warning_no") or "").strip()
            if warning_no and warning_no in self._seen_warning_nos:
                duplicate_records += 1
                continue
            if warning_no:
                self._seen_warning_nos.add(warning_no)

            record = self._normalize_record(record)
            enriched.append(record)

        if empty_records:
            logger.info(
                "[NgaNavwarnAdapter] Filtered %d empty records on page %d",
                empty_records, page,
            )

        if duplicate_records:
            logger.info(
                "[NgaNavwarnAdapter] Filtered %d duplicate records on page %d",
                duplicate_records, page,
            )

        if self._retry_count > 0:
            logger.info(
                "[NgaNavwarnAdapter] ✓ Page %d recovered after %d retries "
                "(total errors: %d), got %d records",
                page, self._retry_count, self._error_count, len(enriched),
            )
            self._retry_count = 0

        return enriched

    def _normalize_record(self, record: dict) -> dict:
        """标准化记录字段。"""
        record["data_source"] = "nga"
        record["data_type"] = "navwarn"

        area = str(record.get("area") or "").strip()
        if area:
            record["area"] = self._normalize_area(area)

        issue_time = str(record.get("issue_time") or "").strip()
        if issue_time:
            record["issue_time"] = self._normalize_time(issue_time)

        effective_time = str(record.get("effective_time") or "").strip()
        if effective_time:
            record["effective_time"] = self._normalize_time(effective_time)

        message_url = record.get("message_url")
        if message_url and not message_url.startswith("http"):
            record["message_url"] = f"{self._base_url}{message_url}"

        return record

    @staticmethod
    def _normalize_area(area: str) -> str:
        """标准化区域名称。"""
        area_map = {
            "NAVAREA I": "NAVAREA I",
            "NAVAREA II": "NAVAREA II",
            "NAVAREA III": "NAVAREA III",
            "NAVAREA IV": "NAVAREA IV",
            "NAVAREA V": "NAVAREA V",
            "NAVAREA VI": "NAVAREA VI",
            "NAVAREA VII": "NAVAREA VII",
            "NAVAREA VIII": "NAVAREA VIII",
            "NAVAREA IX": "NAVAREA IX",
            "NAVAREA X": "NAVAREA X",
            "NAVAREA XI": "NAVAREA XI",
            "NAVAREA XII": "NAVAREA XII",
            "NAVAREA XIII": "NAVAREA XIII",
            "NAVAREA XIV": "NAVAREA XIV",
            "NAVAREA XV": "NAVAREA XV",
            "NAVAREA XVI": "NAVAREA XVI",
            "NAVAREA XVII": "NAVAREA XVII",
            "NAVAREA XVIII": "NAVAREA XVIII",
            "NAVAREA XIX": "NAVAREA XIX",
            "NAVAREA XX": "NAVAREA XX",
        }
        return area_map.get(area.upper(), area)

    @staticmethod
    def _normalize_time(time_str: str) -> str:
        """标准化时间格式。"""
        return time_str.strip()

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
        """处理 NGA 特有错误，带重试上限保护。

        策略：
        - 连接超时 / 网络错误 → 优先换代理，无代理则等待重试
        - 429 / 503          → 延长等待后重试
        - 404                → 该页无数据，跳过
        - 超出最大重试次数    → 放弃当前页
        """
        self._error_count += 1
        err_short = str(error)[:100]

        if attempt >= _MAX_RETRIES:
            logger.error(
                "[NgaNavwarnAdapter] ✗ Page %d GAVE UP after %d attempts "
                "(cumulative errors: %d). Last error: %s",
                page, attempt, self._error_count, err_short,
            )
            return "skip"

        error_str = str(error)

        if "404" in error_str:
            logger.info("[NgaNavwarnAdapter] Page %d returned 404, skipping", page)
            return "skip"

        if "429" in error_str or "503" in error_str:
            wait = min(60, 10 * (attempt + 1))
            self._retry_count += 1
            logger.warning(
                "[NgaNavwarnAdapter] ⏳ Page %d RATE LIMITED "
                "[retry %d/%d, total errors: %d] — waiting %ds then retry",
                page, attempt + 1, _MAX_RETRIES,
                self._error_count, wait,
            )
            await asyncio.sleep(wait)
            return None

        is_network_error = any(p in error_str for p in _RETRYABLE_PATTERNS)

        if is_network_error:
            proxy_switched = False
            if self._client is not None:
                try:
                    await self._client.mark_last_proxy_failed(self.adapter_name)
                    proxy_switched = True
                except Exception:
                    pass

            base_wait = min(60, 10 * (2 ** attempt))
            jitter = random.uniform(0, base_wait * 0.3)
            wait = base_wait + jitter
            self._retry_count += 1

            proxy_status = "proxy switched" if proxy_switched else "no proxy pool, direct retry"
            logger.warning(
                "[NgaNavwarnAdapter] ⏳ Page %d CONNECTION ERROR "
                "[retry %d/%d, total errors: %d, %s] — wait %.1fs then retry\n"
                "    └─ %s",
                page, attempt + 1, _MAX_RETRIES,
                self._error_count, proxy_status, wait,
                err_short,
            )
            await asyncio.sleep(wait)
            return None

        wait = min(30, 5 * (attempt + 1))
        self._retry_count += 1
        logger.warning(
            "[NgaNavwarnAdapter] ⏳ Page %d UNKNOWN ERROR "
            "[retry %d/%d, total errors: %d] — wait %ds then retry\n"
            "    └─ %s",
            page, attempt + 1, _MAX_RETRIES,
            self._error_count, wait, err_short,
        )
        await asyncio.sleep(wait)
        return None
