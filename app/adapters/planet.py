"""Planet 适配器 — 处理 Planet4589 太空论文采集，支持嵌套目录递归。

核心逻辑
--------
planet4589.org 是 Jonathan McDowell 的个人太空研究站点, 提供静态文件列表页
(nginx autoindex)。页面为纯 HTML 表格, 无需特殊信令或反爬处理。

URL 规则:
- 列表页: /space/papers/?sort={sort}&order={order}
- 文件:   /space/papers/{filename}.pdf
- 子目录: /space/papers/{dirname}/ → 递归采集

本适配器处理:
1. 请求头伪装（模拟普通浏览器访问）
2. 递归采集嵌套目录中的 PDF 文件
3. 域名参数化支持（domain 参数可调整）
4. 过滤 "Up" 链接和非 PDF 文件
5. 相对路径 URL 规范化到绝对地址
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin
from typing import Any

from app.adapters import BaseSiteAdapter, register_adapter
from app.downloader.http_client import HttpClient
from app.parser.template_parser import TemplateParser

logger = logging.getLogger(__name__)

# 递归最大深度，防止无限循环
_MAX_CRAWL_DEPTH = 5


@register_adapter("planet")
class PlanetAdapter(BaseSiteAdapter):
    """Planet 太空论文站点适配器，支持嵌套目录递归采集。"""

    adapter_name = "planet"

    def __init__(
        self,
        base_url: str,
        http_client: HttpClient | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, http_client, **kwargs)
        self._template: Any = None
        self._parser = TemplateParser()
        self._total_pdfs = 0

    async def on_before_crawl(self, template: Any) -> None:
        """采集开始前：缓存模板引用和列表页解析所需的字段配置。"""
        await super().on_before_crawl(template)
        self._template = template
        logger.info(
            "[PlanetAdapter] ▶ Starting crawl: base_url=%s, list_page=%s",
            self._base_url, template.list_page,
        )

    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:
        """每页数据返回后：过滤记录，递归进入子目录采集 PDF。"""
        if not records:
            return records

        # 首页的基 URL 从模板 list_page 推导
        list_page = self._template.list_page
        base_dir = list_page.split("?")[0].rstrip("/") + "/"
        base_dir_url = urljoin(self._base_url, base_dir)

        filtered = await self._process_records(records, depth=0, current_dir_url=base_dir_url)
        return filtered

    async def _process_records(
        self,
        records: list[dict],
        depth: int,
        current_dir_url: str,
    ) -> list[dict]:
        """递归处理记录列表，遇到目录则深入采集。

        Args:
            records: 当前页面的解析记录。
            depth: 当前递归深度，超过 _MAX_CRAWL_DEPTH 则停止。
            current_dir_url: 当前目录的完整 URL，用于 URL 规范化。
        """
        if depth >= _MAX_CRAWL_DEPTH:
            logger.warning(
                "[PlanetAdapter] Max recursion depth (%d) reached, "
                "skipping subdirectories",
                _MAX_CRAWL_DEPTH,
            )
            return []

        result: list[dict] = []

        for record in records:
            name = record.get("name", "").strip()

            # 跳过空文件名
            if not name:
                continue

            # 跳过 "Up" 链接
            if name.lower() == "up":
                continue

            # 子目录 → 递归采集
            if name.endswith("/"):
                sub_records = await self._crawl_subdirectory(
                    record, depth + 1, current_dir_url,
                )
                result.extend(sub_records)
                continue

            # 仅采集 PDF 文件
            if not name.lower().endswith(".pdf"):
                logger.debug(
                    "[PlanetAdapter] Skipping non-PDF file: %s", name,
                )
                continue

            # 规范化 URL：相对路径 → 基于当前目录拼接
            raw_url = record.get("url", "")
            record["url"] = urljoin(current_dir_url, raw_url)

            result.append(record)
            self._total_pdfs += 1

        return result

    async def _crawl_subdirectory(
        self,
        dir_record: dict,
        depth: int,
        current_dir_url: str,
    ) -> list[dict]:
        """递归采集子目录中的 PDF 文件。

        Args:
            dir_record: 目录条目记录，包含 name 和 url 字段。
            depth: 当前递归深度。
            current_dir_url: 父目录的完整 URL。
        """
        dir_name = dir_record.get("name", "unknown").strip().rstrip("/")
        raw_url = dir_record.get("url", "")
        # 子目录 URL = 基于当前目录拼接相对路径
        sub_dir_url = urljoin(current_dir_url, raw_url)

        logger.info(
            "[PlanetAdapter] ▶ Entering subdirectory depth=%d: %s → %s",
            depth, dir_name, sub_dir_url,
        )

        try:
            html = await self._client.request_page(
                sub_dir_url,
                self._template.list_request,
                anti_crawl_enabled=self._template.effective_anti_crawl_enabled,
            )
        except Exception as e:
            logger.warning(
                "[PlanetAdapter] Failed to fetch subdirectory '%s': %s",
                sub_dir_url, e,
            )
            return []

        records = self._parser.parse_list(html, self._template.list_fields)
        logger.debug(
            "[PlanetAdapter] Subdirectory '%s': parsed %d records",
            dir_name, len(records),
        )

        return await self._process_records(records, depth, sub_dir_url)

    def on_request_headers(self, page: int) -> dict[str, str]:
        """注入请求头 — 模拟普通浏览器。"""
        return {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "no-cache",
        }

    async def on_error(
        self, error: Exception, page: int, attempt: int,
    ) -> str | None:
        """处理 planet 特有错误。

        - 404 → 该路径不存在, 跳过
        - 其他 → 默认重试
        """
        error_str = str(error)
        if "404" in error_str:
            logger.info(
                "[PlanetAdapter] Path not found (404), skipping",
            )
            return "skip"
        return None

    @classmethod
    def build_batch_param_value(
        cls, batch_data: list[str], param_name: str,
    ) -> str:
        """参数拼接：取第一条。"""
        if not batch_data:
            return ""
        return batch_data[0]