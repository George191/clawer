"""SSC (Space Systems Command) Press Releases adapter.

Media Room 列表页 (/Connect-With-Us/Media-Room) 是手工编排的 HTML，
结构不规则（日期和链接混在 <p>/<span> 中），无法用模板选择器提取。
adapter 在 on_before_crawl 中手动解析列表页，提取所有 press release 链接。

详情页与 Newsroom 共用同一套模板（DNN ArticleCS），
SSC 页面结构处理放在 app.adapters.utils.news.ssc.common 中。

部分老 press release 直接链接到 PDF 文件（无详情页），
这类记录直接保存 PDF 链接作为附件。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any
from urllib.parse import urljoin

from lxml import html as lxml_html

from app.adapters import register_adapter
from app.adapters.utils.news import NewsBaseAdapter
from app.adapters.utils.news.ssc import common as ssc_common
from app.downloader.http_client import HttpClient
from app.models.template import RequestConfig

logger = logging.getLogger(__name__)

_DETAIL_CONCURRENCY = 4


@register_adapter("ssc_press")
class SscPressAdapter(NewsBaseAdapter):
    """SSC Press Releases adapter — 手动解析 Media Room 列表页。"""

    adapter_name = "ssc_press"
    site_domain = "ssc.spaceforce.mil"

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._template: Any = None
        self._press_links: list[dict] = []

    async def on_before_crawl(self, template: Any) -> None:
        """爬取开始前：手动解析 Media Room 列表页。"""
        await super().on_before_crawl(template)
        self._template = template

        if not self._client:
            return

        try:
            url = f"{self._base_url}/Connect-With-Us/Media-Room"
            cfg = RequestConfig(
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                }
            )
            html_content = await self._client.request_page(
                url, cfg, anti_crawl_enabled=False,
            )
            self._press_links = self._parse_media_room(html_content)
            logger.info(
                "[SscPress] Parsed %d press links from Media Room",
                len(self._press_links),
            )
        except Exception as e:
            logger.warning(
                "[SscPress] Failed to parse Media Room: %s",
                str(e)[:100],
            )

    def _parse_media_room(self, html: str) -> list[dict]:
        """解析 Media Room 页面，提取所有新闻稿链接。

        页面结构：
        - 年份 Tab（2026, 2025, 2024, ...）不改变 URL，纯前端切换
        - 所有年份内容在同一页面中，通过 h3/h4/p 标记月份分组
        - 月份标题格式如 "JUNE 2026"、"DECEMBER 2025"，已包含年份
        - 部分老新闻直接链接到 PDF 文件
        """
        try:
            tree = lxml_html.fromstring(html)
        except Exception:
            return []

        links: list[dict] = []
        seen: set[str] = set()
        current_month = ""

        # 找到内容区域（Tab 容器内的所有内容）
        tab_container = tree.cssselect("#dnn_ctr2345_ViewTabs_pnlContainter")
        container = tab_container[0] if tab_container else tree

        # 遍历所有元素，跟踪月份分组
        for elem in container.iter():
            # 检测月份标题
            if elem.tag in ("h3", "h4", "p"):
                text = elem.text_content().strip().upper()
                month_match = re.match(
                    r"^(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+(\d{4})",
                    text,
                )
                if month_match:
                    current_month = f"{month_match.group(1)} {month_match.group(2)}"

            # 提取链接
            if elem.tag == "a" and elem.get("href"):
                href = (elem.get("href") or "").strip()
                title = elem.text_content().strip()

                if not href or not title:
                    continue

                is_article = "/Newsroom/Article/" in href
                is_pdf = href.lower().endswith(".pdf") or "/Portals/3/" in href or "LinkClick.aspx" in href

                if not (is_article or is_pdf):
                    continue

                full_url = urljoin(f"{self._base_url}/", href)

                if full_url in seen:
                    continue
                seen.add(full_url)

                date = self._extract_date_from_context(elem)

                record: dict[str, Any] = {
                    "title": title,
                    "url": full_url,
                    "link_type": "press_release",
                }

                # 时间分组信息（从月份标题提取）
                if current_month:
                    record["month_group"] = current_month
                    year = current_month.split()[-1]
                    if year:
                        record["year_group"] = year

                if date:
                    record["date"] = date

                # PDF 链接标记
                if is_pdf and not is_article:
                    record["is_pdf"] = True
                    ext = full_url.rsplit(".", 1)[-1].lower() if "." in full_url else "pdf"
                    record["attachments"] = [{
                        "url": full_url,
                        "type": ext,
                        "label": title,
                    }]

                links.append(record)

        return links

    @staticmethod
    def _extract_date_from_context(a_tag: Any) -> str:
        """从链接周围的文本中提取日期（如 '09 JUN 2026 -'）。"""
        current = a_tag.getparent()
        for _ in range(3):
            if current is None:
                break
            text = current.text_content().strip()
            match = re.search(
                r"(\d{1,2}\s+[A-Z]{3}\s+\d{4}|\w+\s+\d{1,2},?\s+\d{4})",
                text,
            )
            if match:
                return match.group(1)
            current = current.getparent()
        return ""

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """覆盖默认行为：用 _press_links 替换模板解析的记录，逐条请求详情页。

        Media Room 是单页 HTML（年份 Tab 不改变 URL），
        所有数据在 on_before_crawl 中已解析完毕。
        只在第 1 页返回数据，第 2 页起返回空列表以停止翻页。
        """
        records = await super().on_after_page(page, records)

        # 只在第 1 页处理；第 2 页起返回空列表停止翻页
        if page > 1:
            return []

        # 如果 on_before_crawl 成功解析了列表页，使用手动解析的结果
        if self._press_links:
            records = list(self._press_links)
        else:
            # fallback: 使用模板解析的记录
            for r in records:
                r["link_type"] = "press_release"

        if not records:
            return []

        # 逐条请求详情页（非 PDF 记录）
        semaphore = asyncio.Semaphore(_DETAIL_CONCURRENCY)

        async def enrich(record: dict) -> dict:
            async with semaphore:
                return await self._enrich_detail(record)

        return list(await asyncio.gather(*(enrich(record) for record in records)))

    async def _enrich_detail(self, record: dict) -> dict:
        """请求详情页并提取字段。PDF 记录跳过。"""
        if record.get("is_pdf"):
            return record

        detail_url = record.get("url", "")
        if not detail_url or "/Newsroom/Article/" not in detail_url:
            return record

        try:
            cfg = RequestConfig(
                headers={
                    "Accept": "text/html,application/xhtml+xml",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
            html = await self._client.request_page(
                detail_url, cfg, anti_crawl_enabled=False,
            )
        except Exception as e:
            logger.warning(
                "[SscPress] Failed to fetch detail '%s': %s",
                detail_url, str(e)[:80],
            )
            return record

        content_field_selector = self.detail_field_selector(self._template, "content")
        ssc_common.extract_meta_fields(html, record)
        ssc_common.extract_content(html, record, detail_url, content_field_selector)
        ssc_common.extract_slides(html, record, detail_url, content_field_selector)
        ssc_common.extract_figures(html, record, detail_url, content_field_selector)
        ssc_common.extract_attachments(html, record, detail_url, content_field_selector)
        ssc_common.extract_tags(html, record)
        ssc_common.extract_external_links(self, record, detail_url)

        # 确保 link_type
        record["link_type"] = "press_release"

        return record

    def on_request_headers(self, page: int) -> dict[str, str]:
        return {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "no-cache",
        }

    async def on_error(
        self, error: Exception, page: int, attempt: int,
    ) -> str | None:
        error_str = str(error)
        if "404" in error_str:
            return "skip"
        if "403" in error_str:
            return "skip"
        return None
