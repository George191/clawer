"""BlackSky 新闻稿适配器 (Press Releases / releases CPT)。

核心逻辑
--------
1. 通过 WordPress REST API /wp-json/wp/v2/releases 翻页采集新闻稿列表
2. releases 类型的 content.rendered 有完整 HTML 正文
3. 正文处理（图片/附件/外链/纯文本）由基类 _process_content_html 统一完成
4. featured_media 通过基类 _enrich_cover_images_batch 并行获取封面图
5. 清理所有 WP API 中间字段
"""

from __future__ import annotations

import logging
from typing import Any

from app.adapters import register_adapter
from app.adapters.utils.news.blacksky_base import BlackSkyBaseAdapter
from app.downloader.http_client import HttpClient

logger = logging.getLogger(__name__)


@register_adapter("blacksky_press")
class BlackSkyPressAdapter(BlackSkyBaseAdapter):
    """BlackSky 新闻稿适配器（WP REST API /releases CPT）。"""

    adapter_name = "blacksky_press"

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """列表页后处理：正文提取、封面图、字段清理。"""
        records = await super().on_after_page(page, records)
        if not records:
            return records

        # 并行获取封面图
        await self._enrich_cover_images_batch(records)

        for record in records:
            record["link_type"] = "press_release"

            # excerpt → summary
            excerpt_html = str(record.get("excerpt") or "").strip()
            if excerpt_html:
                record["summary"] = self.html_to_text(excerpt_html)

            # 正文处理：图片/附件/外链/纯文本
            url = str(record.get("url") or self._base_url)
            await self._process_content_html(record, url)

            # 清理 WP API 中间字段
            self._cleanup_wp_fields(record)

        return records
