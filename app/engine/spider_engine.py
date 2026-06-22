"""蜘蛛引擎 — 核心采集引擎，负责页面抓取、JSON/HTML 解析与翻页控制。

工作流程
--------
1. 加载 SiteTemplate 配置
2. 遍历列表页（支持 JSON API 和 HTML 两种响应类型）
3. 解析每条记录（支持详情页补充字段）
4. 通过 StorageBackend 保存采集结果
5. 支持断点续采（Redis checkpoint）和站点适配器扩展

设计原则
--------
- 引擎仅负责采集逻辑，不包含下载/MinIO/Kafka 等下游操作
- 无限重试 + 指数退避，确保不因临时故障中断翻页
- 适配器模式扩展站点特定行为（如 Google Patents 信令）
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any

from app.config.settings import settings
from app.downloader.http_client import HttpClient
from app.adapters import get_adapter
from app.models.template import (
    PaginationType,
    ResponseType,
    SiteTemplate,
)
from app.parser.template_parser import TemplateParser, resolve_json_path
from app.storage.file_storage import FileStorage, StorageBackend

logger = logging.getLogger(__name__)

# 增强模块（延迟导入避免循环依赖）
_renderer = None
_hook_mgr = None

def _init_enhancements():
    """延迟初始化增强模块。"""
    global _renderer, _hook_mgr

    if settings.jinja2_enabled and _renderer is None:
        from app.engine.jinja2_renderer import get_jinja2_renderer
        _renderer = get_jinja2_renderer()
    if settings.pre_hooks_enabled and _hook_mgr is None:
        from app.engine.jinja2_renderer import get_prehook_manager
        _hook_mgr = get_prehook_manager()


def _create_storage() -> StorageBackend:
    if settings.db_url:
        from app.storage.mongo_storage import MongoStorage
        logger.info("Using MongoDB storage backend")
        return MongoStorage()
    logger.info("Using File storage backend (no MongoDB configured)")
    return FileStorage()


class CrawlResult:
    def __init__(self, template_name: str, data_type: str) -> None:
        self.template_name = template_name
        self.data_type = data_type
        self.records: list[dict[str, Any]] = []
        self.downloaded_files: list[str] = []
        self.errors: list[str] = []

    @property
    def total(self) -> int:
        return len(self.records)

    @property
    def success(self) -> bool:
        return len(self.errors) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template_name,
            "data_type": self.data_type,
            "total_records": self.total,
            "downloaded_files": len(self.downloaded_files),
            "errors": self.errors,
            "success": self.success,
        }


class SpiderEngine:
    def __init__(
        self,
        http_client: HttpClient | None = None,
        parser: TemplateParser | None = None,
        storage: StorageBackend | None = None,
    ) -> None:
        self._client = http_client or HttpClient()
        self._parser = parser or TemplateParser()
        self._storage = storage or _create_storage()
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)
        # 断点续采过期时间（默认7天）

    async def _save_page_records(
        self,
        template: SiteTemplate,
        records: list[dict[str, Any]],
        result: CrawlResult,
    ) -> None:
        search_params = getattr(template, "_param_values", None) or {}
        if search_params:
            for record in records:
                record["_meta_search_params"] = search_params
        await self._storage.save_records(template.name, template.data_type, template.dedup_fields, records)
        result.records.extend(records)
        logger.info("Saved %d records, cumulative: %d", len(records), len(result.records))

    async def crawl(self, template: SiteTemplate) -> CrawlResult:
        result = CrawlResult(template.name, template.data_type)
        resume_page: int | None = None

        logger.info(
            "Starting crawl: template=%s, data_type=%s, response_type=%s, priority=%d%s",
            template.name,
            template.data_type,
            template.response_type.value,
            template.priority,
            f" (resume from page {resume_page})" if resume_page else "",
        )

        try:
            _init_enhancements()
            if _hook_mgr is not None and _hook_mgr.enabled and template.pre_hooks:
                hook_names = [h.name for h in template.pre_hooks]
                hook_context: dict = {}
                for h in template.pre_hooks:
                    hook_context.update(h.args)
                hook_context = await _hook_mgr.execute(hook_names, hook_context)
                logger.info("Pre-hooks completed, context: %s", list(hook_context.keys()))

            list_records = await self._crawl_list_pages(
                template, result, resume_page
            )

            result.records = list_records

        except Exception as e:
            logger.error("Crawl failed for template %s: %s", template.name, e)
            result.errors.append(str(e))

        logger.info(
            "Crawl complete: template=%s, records=%d, errors=%d",
            template.name,
            result.total,
            len(result.errors),
        )
        return result

    async def _crawl_list_pages(
        self, template: SiteTemplate, result: CrawlResult,
        resume_page: int | None = None,
    ) -> list[dict[str, Any]]:
        if template.response_type == ResponseType.JSON:
            return await self._crawl_list_pages_json(template, result, resume_page)
        return await self._crawl_list_pages_html(template, result, resume_page)


    async def _crawl_list_pages_html(
        self, template: SiteTemplate, result: CrawlResult,
        resume_page: int | None = None,
    ) -> list[dict[str, Any]]:
        all_records: list[dict[str, Any]] = []
        page = resume_page if resume_page is not None else (
            template.list_pagination.start_page if template.list_pagination else 1
        )
        max_pages = template.list_pagination.max_pages if template.list_pagination else 1
        results_per_page = template.list_pagination.results_per_page if template.list_pagination else 100

        # 初始化适配器（与 JSON 路径一致，支持站点特定行为和重试逻辑）
        adapter = get_adapter(
            template.adapter, template.base_url, self._client
        )
        await adapter.on_before_crawl(template)

        # max_pages=0 表示不限制页数，使用大数代替无限循环
        has_page_cap = max_pages > 0
        pages_crawled = 0
        current_page = page

        while not has_page_cap or pages_crawled < max_pages:
            is_first = (current_page == page)
            page_succeeded = False
            page_skipped = False
            records: list[dict[str, Any]] = []  # 防止 skip 路径下未定义

            for attempt in self._retry_loop():
                try:
                    # 重试时打印提示
                    if attempt > 0:
                        logger.info(
                            "[%s] ↻ Retry page %d (attempt #%d)",
                            template.name, current_page, attempt + 1,
                        )

                    await adapter.on_before_page(current_page, is_first)

                    url = template.get_full_list_url(current_page, results_per_page)
                    html = await self._client.request_page(url, template.list_request, anti_crawl_enabled=template.effective_anti_crawl_enabled)
                    records = self._parser.parse_list(html, template.list_fields)

                    records = await adapter.on_after_page(current_page, records)

                    if not records:
                        logger.info(
                            "No more records at page %d, stopping pagination",
                            current_page,
                        )
                        page_succeeded = True  # 正常结束，非错误
                        break  # 跳出重试循环

                    _init_enhancements()
                    
                    if records:
                        await self._save_page_records(template, records, result)

                    all_records.extend(records)

                    logger.info(
                        "Page %d: found %d records (total: %d)",
                        current_page,
                        len(records),
                        len(all_records),
                    )

                    page_succeeded = True
                    break  # 页面成功，跳出重试循环

                except Exception as e:
                    logger.warning(
                        "[%s] ✗ Page %d failed (attempt %d): %s",
                        template.name, current_page, attempt + 1,
                        str(e)[:150],
                    )

                    adapter_action = await adapter.on_error(e, current_page, attempt)

                    if adapter_action == "abort":
                        result.errors.append(f"List page {current_page}: {e}")
                        await adapter.close()
                        return all_records
                    elif adapter_action == "reset_session":
                        await adapter.on_before_crawl(template)
                        continue
                    elif adapter_action == "skip":
                        logger.info(
                            "[%s] ⊘ Page %d skipped by adapter (attempt %d)",
                            template.name, current_page, attempt + 1,
                        )
                        page_succeeded = True  # 标记为已处理
                        page_skipped = True
                        break
                    # None → 继续下一次重试

            if not page_succeeded:
                logger.error(
                    "[%s] ✗ Page %d FAILED after all retry attempts, moving on",
                    template.name, current_page,
                )
                result.errors.append(f"List page {current_page}: exceeded retries")
                break  # 超出重试次数，中断翻页

            if page_succeeded and not page_skipped and not records:
                break

            if not template.list_pagination:
                break

            if template.list_pagination.type == PaginationType.NEXT_PAGE:
                has_next = self._parser.extract_links(
                    html,
                    template.list_pagination.next_selector or "",
                    template.detail_url_selector_type,
                )
                if not has_next:
                    break

            adapter.on_page_advance()
            pages_crawled += 1
            current_page += 1

        await adapter.close()
        return all_records

    async def _crawl_list_pages_json(
        self, template: SiteTemplate, result: CrawlResult,
        resume_page: int | None = None,
    ) -> list[dict[str, Any]]:
        all_records: list[dict[str, Any]] = []
        start_page = resume_page if resume_page is not None else (
            template.list_pagination.start_page if template.list_pagination else 0
        )
        config_max_pages = template.list_pagination.max_pages if template.list_pagination else 0
        results_per_page = template.list_pagination.results_per_page if template.list_pagination else 100
        item_path = template.json_item_path or ""
        page_concurrency = (
            template.list_pagination.page_concurrency
            if template.list_pagination and template.list_pagination.page_concurrency
            else settings.page_concurrency
        )

        # max_pages=0 表示"不限"（由 API 返回值或空页决定终止）
        has_page_cap = config_max_pages > 0
        dynamic_pages: int | float = config_max_pages if has_page_cap else float("inf")

        adapter = get_adapter(template.adapter, template.base_url, self._client)
        await adapter.on_before_crawl(template)

        # ── Phase 1: 获取第一页，确定总页数 ─────────────────────────
        page1, records1, total_records, total_pages_from_api, abort = await self._fetch_page_json(
            template, start_page, adapter, results_per_page, item_path,
            is_first=True, result=result,
        )
        if abort:
            await adapter.close()
            return all_records

        # 从第一页响应计算总页数
        total_for_log = ""
        if total_records is not None:
            total_for_log = f" / total={total_records}"
            dynamic_pages = (total_records + results_per_page - 1) // results_per_page
            if total_pages_from_api is not None:
                dynamic_pages = min(dynamic_pages, total_pages_from_api)
            if has_page_cap:
                dynamic_pages = min(config_max_pages, dynamic_pages)
            logger.info(
                "Dynamic pagination: total=%d, per_page=%d, need %d pages%s",
                total_records, results_per_page, dynamic_pages,
                f" (capped at {config_max_pages})" if has_page_cap else "",
            )
        elif total_pages_from_api is not None:
            dynamic_pages = total_pages_from_api
            if has_page_cap:
                dynamic_pages = min(config_max_pages, dynamic_pages)
            logger.info(
                "API reports %d total pages%s",
                dynamic_pages,
                f" (capped at {config_max_pages})" if has_page_cap else "",
            )

        # 处理第一页记录
        _init_enhancements()
        all_records.extend(records1)
        if records1:
            await self._save_page_records(template, records1, result)

        logger.info(
            "Page %d/%s: found %d records%s (cumulative: %d)",
            start_page + 1, dynamic_pages,
            len(records1), total_for_log, len(all_records),
        )

        # 第一页终止条件
        if not records1:
            await adapter.close()
            return all_records
        if len(records1) < results_per_page:
            await adapter.close()
            return all_records

        # ── Phase 2: 并行获取剩余页面 ───────────────────────────────
        known_total = isinstance(dynamic_pages, int) and dynamic_pages > start_page + 1

        if known_total:
            # 已知总页数：一次性计算所有剩余页面并分批并行获取
            remaining_pages = list(range(start_page + 1, int(dynamic_pages)))
            for batch_start in range(0, len(remaining_pages), page_concurrency):
                batch = remaining_pages[batch_start:batch_start + page_concurrency]
                await self._fetch_and_process_batch(
                    template, batch, adapter, results_per_page, item_path,
                    dynamic_pages, result, all_records
                )
        else:
            # 未知总页数：逐批并行获取，遇空页或不足一页时终止
            current = start_page + 1
            while True:
                batch = list(range(current, current + page_concurrency))
                should_stop = await self._fetch_and_process_batch(
                    template, batch, adapter, results_per_page, item_path,
                    dynamic_pages, result, all_records
                )
                if should_stop:
                    break
                current += page_concurrency

        await adapter.close()
        return all_records

    async def _fetch_page_json(
        self,
        template: SiteTemplate,
        page: int,
        adapter: Any,
        results_per_page: int,
        item_path: str,
        is_first: bool,
        result: CrawlResult,
    ) -> tuple[int, list[dict[str, Any]], int | None, int | None, bool]:
        """获取单个 JSON 列表页，带重试。

        Returns:
            (page, records, total_records, total_pages_from_api, abort)
        """
        page_succeeded = False
        page_skipped = False
        records: list[dict[str, Any]] = []
        total_records: int | None = None
        total_pages_from_api: int | None = None

        for attempt in self._retry_loop():
            try:
                if attempt > 0:
                    logger.info(
                        "[%s] ↻ Retry page %d (attempt #%d)",
                        template.name, page, attempt + 1,
                    )

                await adapter.on_before_page(page, is_first)

                _session = getattr(adapter, "_session", None)
                url = template.get_full_list_url(
                    page, num=results_per_page,
                    peid=_session.eid if _session else None,
                )
                extra_headers = adapter.on_request_headers(page)
                list_request = template.list_request.model_copy(update={
                    "headers": {**template.list_request.headers, **extra_headers}
                }) if extra_headers else template.list_request

                text = await self._client.request_page(
                    url, list_request,
                    anti_crawl_enabled=template.effective_anti_crawl_enabled,
                )
                json_data = json.loads(text)

                records = self._parser.parse_list_json(
                    json_data, item_path, template.list_fields
                )
                records = await adapter.on_after_page(page, records)

                # 仅第一页提取分页元数据
                if is_first and template.json_total_path:
                    total_val = resolve_json_path(json_data, template.json_total_path)
                    if total_val is not None:
                        try:
                            total_records = int(total_val)
                        except (ValueError, TypeError):
                            pass

                if is_first and template.json_total_num_pages:
                    api_pages_val = resolve_json_path(
                        json_data, template.json_total_num_pages
                    )
                    if api_pages_val is not None:
                        try:
                            total_pages_from_api = int(api_pages_val)
                        except (ValueError, TypeError):
                            pass

                page_succeeded = True
                break

            except json.JSONDecodeError:
                await self._client.mark_last_proxy_failed()

            except Exception as e:
                logger.warning(
                    "[%s] ✗ Page %d failed (attempt %d): %s",
                    template.name, page, attempt + 1,
                    str(e)[:150],
                )

                adapter_action = await adapter.on_error(e, page, attempt)
                if adapter_action == "abort":
                    result.errors.append(f"List page {page}: {e}")
                    return page, [], None, None, True
                elif adapter_action == "reset_session":
                    await adapter.on_before_crawl(template)
                    continue
                elif adapter_action == "skip":
                    page_skipped = True
                    page_succeeded = True
                    break
                # None → 继续下一次重试

        if not page_succeeded:
            logger.error(
                "Page %d failed after %d attempts, skipping",
                page, settings.http_max_retries,
            )
            result.errors.append(f"List page {page}: exceeded retries")

        if page_skipped:
            records = []

        return page, records, total_records, total_pages_from_api, False

    async def _fetch_and_process_batch(
        self,
        template: SiteTemplate,
        batch: list[int],
        adapter: Any,
        results_per_page: int,
        item_path: str,
        dynamic_pages: int | float,
        result: CrawlResult,
        all_records: list[dict],
    ) -> bool:
        """并行获取一批页面，处理去重和保存。

        Returns:
            True 如果应该停止翻页（空页或不足一页），否则 False。
        """
        tasks = [
            self._fetch_page_json(
                template, p, adapter, results_per_page, item_path,
                is_first=False, result=result,
            )
            for p in batch
        ]
        batch_results = await asyncio.gather(*tasks)

        should_stop = False
        for p, records, _, _, abort in batch_results:
            if abort:
                continue

            all_records.extend(records)
            if records:
                await self._save_page_records(template, records, result)

            logger.info(
                "Page %d/%s: found %d records (cumulative: %d)",
                p + 1, dynamic_pages, len(records), len(all_records),
            )

            if not records or len(records) < results_per_page:
                should_stop = True

        return should_stop

    @staticmethod
    def _retry_loop():
        return itertools.count()

    async def close(self) -> None:
        await self._client.close()
        if hasattr(self._storage, "close"):
            await self._storage.close()
