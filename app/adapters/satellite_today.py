"""SatelliteToday 新闻适配器。

核心逻辑
--------
1. 通过 WordPress REST API 抓取全站 post 列表，覆盖全部 topic/category
2. 利用 categories/tags API 将 taxonomy ID 映射为名称和 slug
3. 正文处理（图片/附件/外链）由 WordPress 新闻工具统一完成
4. 通过新闻通用层并行获取封面图 URL
5. 清理所有 WP API 中间字段，删除冗余的 topic_* 字段
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.adapters import register_adapter
from app.adapters.utils.news import NewsBaseAdapter
from app.adapters.utils.news.wp import assets as wp_assets
from app.downloader.http_client import HttpClient

logger = logging.getLogger(__name__)


@register_adapter("satellite_today")
class SatelliteTodayAdapter(NewsBaseAdapter):
    """SatelliteToday 新闻适配器。"""

    adapter_name = "satellite_today"
    site_domain = "satellitetoday.com"

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._category_map: dict[int, dict[str, str]] = {}
        self._tag_map: dict[int, dict[str, str]] = {}
        self._media_cache: dict[int, str] = {}

    async def on_before_crawl(self, template: Any) -> None:
        await super().on_before_crawl(template)
        await self._fetch_category_map()

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """列表页后处理：分类/标签映射、正文提取、封面图、字段清理。"""
        records = await super().on_after_page(page, records)
        if not records:
            return records

        # 按需补全缺失的标签映射
        missing_tag_ids: set[int] = set()
        for record in records:
            for tag_id in record.get("tag_ids") or []:
                if isinstance(tag_id, int) and tag_id not in self._tag_map:
                    missing_tag_ids.add(tag_id)
        if missing_tag_ids:
            await self._fetch_tag_map(missing_tag_ids)

        # 并行获取封面图（仅对 _embed 未返回 image_url 的记录）
        pending_media = [
            record for record in records
            if isinstance(record.get("featured_media"), int)
            and record["featured_media"] > 0
        ]
        if pending_media:
            await wp_assets.enrich_cover_images_batch(
                self._client,
                self._base_url,
                pending_media,
                self._media_cache,
            )

        for record in records:
            # 分类 ID → 名称/slug
            category_ids = record.get("category_ids") or []
            if isinstance(category_ids, list):
                category_meta = [
                    self._category_map[cid]
                    for cid in category_ids
                    if isinstance(cid, int) and cid in self._category_map
                ]
                if category_meta:
                    names = [item["name"] for item in category_meta]
                    slugs = [item["slug"] for item in category_meta]
                    record["category_names"] = names
                    record["category_slugs"] = slugs
                    record["primary_category"] = names

            # 标签 ID → 名称/slug
            tag_ids = record.get("tag_ids") or []
            if isinstance(tag_ids, list):
                tag_meta = [
                    self._tag_map[tid]
                    for tid in tag_ids
                    if isinstance(tid, int) and tid in self._tag_map
                ]
                if tag_meta:
                    record["tag_names"] = [item["name"] for item in tag_meta]
                    record["tag_slugs"] = [item["slug"] for item in tag_meta]

            # 正文处理：图片/附件/外链
            url = str(record.get("url") or self._base_url)
            await wp_assets.process_content_html(self, record, url)
            # 清理 WP API 中间字段
            wp_assets.cleanup_wp_fields(record)

        return records

    async def _fetch_category_map(self) -> None:
        """获取全站 topic/category 映射。"""
        if self._category_map:
            return

        try:
            data = await wp_assets.wp_request_json(
                self._client,
                self._base_url,
                f"{self._base_url}/wp-json/wp/v2/categories"
                "?per_page=100&page=1&_fields=id,name,slug",
            )
        except Exception as e:
            logger.warning("[SatelliteToday] Failed to fetch categories: %s", str(e)[:120])
            return

        if not isinstance(data, list):
            return

        for item in data:
            if not isinstance(item, dict):
                continue
            cid = item.get("id")
            slug = item.get("slug")
            name = item.get("name")
            if isinstance(cid, int) and isinstance(slug, str) and isinstance(name, str):
                self._category_map[cid] = {"name": name, "slug": slug}

        logger.info(
            "[SatelliteToday] Loaded %d categories/topics",
            len(self._category_map),
        )

    async def _fetch_tag_map(self, tag_ids: set[int]) -> None:
        """按需补全标签 ID→名称/slug 映射。"""
        unresolved = sorted(tid for tid in tag_ids if tid not in self._tag_map)
        if not unresolved:
            return

        for start in range(0, len(unresolved), 100):
            batch = unresolved[start:start + 100]
            try:
                data = await wp_assets.wp_request_json(
                    self._client,
                    self._base_url,
                    f"{self._base_url}/wp-json/wp/v2/tags"
                    f"?include={','.join(str(tid) for tid in batch)}"
                    f"&per_page={len(batch)}&_fields=id,name,slug",
                )
            except Exception as e:
                logger.debug(
                    "[SatelliteToday] Failed to fetch tags batch %s: %s",
                    batch[:5], str(e)[:100],
                )
                continue

            if not isinstance(data, list):
                continue

            for item in data:
                if not isinstance(item, dict):
                    continue
                tid = item.get("id")
                slug = item.get("slug")
                name = item.get("name")
                if isinstance(tid, int) and isinstance(slug, str) and isinstance(name, str):
                    self._tag_map[tid] = {"name": name, "slug": slug}

    def on_request_headers(self, page: int) -> dict[str, str]:
        """SatelliteToday JSON API 请求头。"""
        return {
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{self._base_url}/",
            "Cache-Control": "no-cache",
        }

    async def on_error(
        self, error: Exception, page: int, attempt: int,
    ) -> str | None:
        """错误处理。"""
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
        if "429" in error_str or "503" in error_str or "403" in error_str:
            await asyncio.sleep(5 * (attempt + 1))
            return None
        return None
