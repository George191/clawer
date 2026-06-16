"""SatelliteToday 新闻适配器。

核心逻辑
--------
1. 通过 WordPress REST API 抓取全站 post 列表，覆盖全部 topic/category
2. 利用 categories/tags API 将 taxonomy ID 映射为名称和 slug
3. 将 content/excerpt HTML 清洗为文本，并从正文中提取外链
4. 通过 WP Media API 回填封面图 URL
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.adapters import register_adapter
from app.adapters.utils.news import NewsBaseAdapter
from app.downloader.http_client import HttpClient
from app.models.template import RequestConfig

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
        """列表页后处理：补全分类/标签/正文/外链/封面图。"""
        records = await super().on_after_page(page, records)
        if not records:
            return records

        missing_tag_ids: set[int] = set()
        for record in records:
            for tag_id in record.get("tag_ids") or []:
                if isinstance(tag_id, int) and tag_id not in self._tag_map:
                    missing_tag_ids.add(tag_id)

        if missing_tag_ids:
            await self._fetch_tag_map(missing_tag_ids)

        for record in records:
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
                    record["topic_names"] = names
                    record["topic_slugs"] = slugs
                    record["primary_category"] = names[0]
                    record["primary_topic"] = names[0]

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

            excerpt_html = str(record.get("excerpt_html") or "").strip()
            if excerpt_html:
                record["summary"] = self.html_to_text(excerpt_html)

            content_html = str(record.get("content_html") or "").strip()
            if content_html:
                images, normalized_html = self.extract_images_from_html(
                    content_html, str(record.get("url") or self._base_url),
                )
                if images:
                    record["images"] = images
                    content_html = normalized_html
                    record["content_html"] = normalized_html

                attachments = self.extract_attachment_links(
                    content_html, str(record.get("url") or self._base_url),
                )
                if attachments:
                    record["attachments"] = attachments

                record["content"] = self.html_to_text(content_html)
                external_links = self.extract_external_links(
                    content_html, str(record.get("url") or self._base_url),
                )
                if external_links:
                    record["external_links"] = external_links

            image_url = str(record.get("image_url") or "").strip()
            featured_media = record.get("featured_media")
            if not image_url and isinstance(featured_media, int) and featured_media > 0:
                image_url = await self._fetch_media_url(featured_media)
            if image_url:
                record["image_url"] = image_url
                record.setdefault("cover_image", image_url)
                record.setdefault("thumbnail", image_url)

        return records

    async def _fetch_category_map(self) -> None:
        """获取全站 topic/category 映射。"""
        if self._category_map:
            return

        try:
            data = await self._request_json(
                "https://www.satellitetoday.com/wp-json/wp/v2/categories"
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
                data = await self._request_json(
                    "https://www.satellitetoday.com/wp-json/wp/v2/tags"
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

    async def _fetch_media_url(self, media_id: int) -> str:
        """按需通过 WP Media API 获取封面图 URL。"""
        if media_id in self._media_cache:
            return self._media_cache[media_id]

        try:
            data = await self._request_json(
                "https://www.satellitetoday.com/wp-json/wp/v2/media/"
                f"{media_id}?_fields=source_url,media_details.sizes.full.source_url",
            )
        except Exception as e:
            logger.debug(
                "[SatelliteToday] Failed to fetch media %d: %s",
                media_id, str(e)[:100],
            )
            return ""

        if not isinstance(data, dict):
            return ""

        media_details = data.get("media_details") or {}
        sizes = media_details.get("sizes") or {}
        full = sizes.get("full") or {}
        source_url = str(
            full.get("source_url") or data.get("source_url") or "",
        ).strip()
        if source_url:
            self._media_cache[media_id] = source_url
        return source_url

    async def _request_json(self, url: str) -> Any:
        config = RequestConfig(
            headers={
                "Accept": "application/json",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Referer": "https://www.satellitetoday.com/",
                "Cache-Control": "no-cache",
            },
            encoding="utf-8",
        )
        text = await self._client.request_page(url, config, anti_crawl_enabled=False)
        return json.loads(text)

    def on_request_headers(self, page: int) -> dict[str, str]:
        """SatelliteToday JSON API 请求头。"""
        return {
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.satellitetoday.com/",
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
        if "429" in error_str or "503" in error_str:
            import asyncio
            await asyncio.sleep(5 * (attempt + 1))
            return None
        return None
