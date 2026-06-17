"""BlackSky 站点适配器基类 — 封装 BlackSky 站点特有逻辑。

BlackSky 站点特征
----------------
- 基于 WordPress REST API（/wp-json/wp/v2/）
- 两种 CPT：/news（媒体报道）和 /releases（新闻稿）
- 封面图通过 WP Media API 获取
- 正文处理：图片/附件/外链/纯文本

本基类提供：
1. BlackSky 站点特定的内容提取选择器
2. 封面图并行获取（使用 BlackSky 站点缓存）
3. 统一的 on_error / on_request_headers
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from typing import Any

from app.adapters import register_adapter
from app.adapters.utils.news import NewsBaseAdapter
from app.downloader.http_client import HttpClient
from lxml import etree

logger = logging.getLogger(__name__)

# 需要从正文中移除的噪音元素（通用，非站点特定）
_NOISE_SELECTORS = (
    "script", "style", "noscript", "iframe", "svg", "form",
    "button", "nav", "aside", "footer", "header",
    ".share", ".sharing", ".social", ".social-share",
    ".newsletter", ".subscribe", ".advertisement", ".ads",
    ".related", ".recommended", ".author-box", ".post-meta",
)


@register_adapter("blacksky_base")
class BlackSkyBaseAdapter(NewsBaseAdapter):
    """BlackSky 站点适配器基类。"""

    adapter_name = "blacksky_base"
    site_domain = "blacksky.com"

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._media_cache: dict[int, str] = {}

    # ── BlackSky 站点特定的内容提取 ─────────────────────────

    @staticmethod
    def extract_main_content_html(page_html: str, content_selectors: list[str] | None = None) -> str:
        """从外部来源页面中提取正文 HTML。

        content_selectors: 从模板 content_selector 配置传入的选择器列表（逗号分隔）。
        未传入时直接返回空。
        """
        from lxml import html as lxml_html

        if not page_html or not content_selectors:
            return ""

        try:
            tree = lxml_html.fromstring(page_html)
        except Exception:
            return ""

        best_node = None
        best_score = 0

        for selector in content_selectors:
            try:
                nodes = tree.cssselect(selector)
            except Exception:
                continue
            for node in nodes:
                text = re.sub(r"\s+", " ", node.text_content()).strip()
                if len(text) > best_score:
                    best_score = len(text)
                    best_node = node

        if best_node is None or best_score < 80:
            return ""

        clone = deepcopy(best_node)
        for selector in _NOISE_SELECTORS:
            for node in clone.cssselect(selector):
                node.drop_tree()

        return etree.tostring(clone, encoding="unicode", method="html").strip()

    # ── 封面图（使用 BlackSky 站点缓存）──────────────────────

    async def _enrich_cover_images_batch(
        self,
        records: list[dict],
    ) -> None:
        """批量并行获取封面图 URL。"""
        pending = [
            (record, int(record.get("featured_media") or 0))
            for record in records
            if record.get("featured_media")
        ]
        if not pending:
            return

        import asyncio

        async def _fetch_one(record: dict, mid: int) -> None:
            url = await self._fetch_wp_media_url(mid, self._media_cache)
            if url:
                record["cover_image"] = url
                record.setdefault("thumbnail", url)

        await asyncio.gather(*(_fetch_one(rec, mid) for rec, mid in pending))

    # ── 请求头 & 错误处理 ───────────────────────────────────

    def on_request_headers(self, page: int) -> dict[str, str]:
        """BlackSky JSON API 请求头。"""
        return {
            "Accept": "application/json",
            "Referer": "https://blacksky.com/company/news/",
        }

    async def on_error(
        self, error: Exception, page: int, attempt: int,
    ) -> str | None:
        """BlackSky 站点统一错误处理。"""
        error_str = str(error)
        lowered = error_str.lower()
        if "400" in error_str and (
            "invalid page" in lowered
            or "rest_post_invalid_page_number" in lowered
            or "larger than the number of pages available" in lowered
        ):
            return "abort"
        if "404" in error_str:
            return "abort"
        if "403" in error_str:
            if attempt >= 2:
                return "abort"
            import asyncio
            await asyncio.sleep(3 * (attempt + 1))
            return None
        if "429" in error_str or "503" in error_str:
            import asyncio
            await asyncio.sleep(5 * (attempt + 1))
            return None
        return None
