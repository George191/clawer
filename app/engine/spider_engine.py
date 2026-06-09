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
from pathlib import Path
from typing import Any

from app.config.settings import settings
from app.downloader.http_client import HttpClient
from app.engine.browser_events import BrowserEventEmitter, PageSession
from app.adapters import get_adapter, BaseSiteAdapter, GenericAdapter
from app.models.template import (
    PaginationType,
    ResponseType,
    SiteTemplate,
)
from app.parser.template_parser import TemplateParser, resolve_json_path
from app.storage.file_storage import FileStorage, StorageBackend

logger = logging.getLogger(__name__)

# 增强模块（延迟导入避免循环依赖）
_dedup = None
_renderer = None
_hook_mgr = None

def _init_enhancements():
    """延迟初始化增强模块。"""
    global _dedup, _renderer, _hook_mgr
    if settings.dedup_enabled and _dedup is None:
        from app.dedup.redis_dedup import get_dedup
        _dedup = get_dedup()
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
        self._checkpoint_redis = None
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)

    async def _ensure_checkpoint_redis(self) -> None:
        if self._checkpoint_redis is not None:
            return
        if not settings.redis_url:
            logger.debug("No Redis URL configured, checkpoint disabled")
            return
        try:
            import redis.asyncio as aioredis
            self._checkpoint_redis = aioredis.from_url(
                settings.redis_url, encoding="utf-8", decode_responses=True,
            )
            await self._checkpoint_redis.ping()
            logger.info("Checkpoint Redis connected: %s", settings.redis_url)
        except ImportError:
            logger.warning("redis package not installed, checkpoint disabled")
        except Exception as e:
            logger.warning("Checkpoint Redis connection failed: %s", e)
            self._checkpoint_redis = None

    def _checkpoint_key(self, template_name: str) -> str:
        return f"spider:checkpoint:{template_name}"

    async def _load_checkpoint(self, template_name: str) -> dict[str, Any] | None:
        if self._checkpoint_redis is None:
            return None
        try:
            data = await self._checkpoint_redis.get(self._checkpoint_key(template_name))
            if data:
                return json.loads(data)
        except Exception as e:
            logger.warning("Failed to load checkpoint: %s", e)
        return None

    async def _save_checkpoint(self, template_name: str, checkpoint: dict[str, Any]) -> None:
        if self._checkpoint_redis is None:
            return
        try:
            key = self._checkpoint_key(template_name)
            await self._checkpoint_redis.set(key, json.dumps(checkpoint, default=str), ex=86400 * 7)
        except Exception as e:
            logger.warning("Failed to save checkpoint: %s", e)

    async def _clear_checkpoint(self, template_name: str) -> None:
        if self._checkpoint_redis is None:
            return
        try:
            await self._checkpoint_redis.delete(self._checkpoint_key(template_name))
        except Exception as e:
            logger.warning("Failed to clear checkpoint: %s", e)

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
        await self._storage.save_records(template.name, template.data_type, records)
        result.records.extend(records)
        logger.info("Saved %d records, cumulative: %d", len(records), len(result.records))

    async def crawl(self, template: SiteTemplate) -> CrawlResult:
        result = CrawlResult(template.name, template.data_type)

        await self._ensure_checkpoint_redis()
        checkpoint = await self._load_checkpoint(template.name)

        resume_page: int | None = None
        if checkpoint and checkpoint.get("status") == "running":
            resume_page = checkpoint.get("page", 0) + 1
            logger.info(
                "Resuming from checkpoint: template=%s, page=%d, records_saved=%d",
                template.name, checkpoint.get("page"), checkpoint.get("records_saved", 0),
            )

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

            list_records = await self._crawl_list_pages(template, result, resume_page)

            if template.detail_fields and list_records:
                detail_records = await self._crawl_detail_pages(
                    template, list_records, result
                )
                result.records = detail_records
            else:
                result.records = list_records

            await self._clear_checkpoint(template.name)

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

    def _get_record_id(self, record: dict[str, Any], template: SiteTemplate) -> str:
        """获取记录的唯一 ID（用于去重）。"""
        # 优先使用 id 
        for key in ("id", ):
            if key in record and record[key]:
                return str(record[key])
        # 回退：用内容 hash
        from app.dedup.redis_dedup import RedisDedup
        return RedisDedup.make_content_hash(record)

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

        for current_page in range(page, page + max_pages):
            try:
                url = template.get_full_list_url(current_page, results_per_page)
                html = await self._client.request_page(url, template.list_request)
                records = self._parser.parse_list(html, template.list_fields)

                if not records:
                    logger.info(
                        "No more records at page %d, stopping pagination",
                        current_page,
                    )
                    break

                _init_enhancements()
                if _dedup is not None and _dedup.enabled:
                    new_records = []
                    for record in records:
                        rid = self._get_record_id(record, template)
                        if await _dedup.exists(template.name, rid):
                            logger.debug("Skipping duplicate record: %s", rid)
                            continue
                        await _dedup.mark_seen(template.name, rid)
                        if settings.incremental_mode:
                            content_hash = _dedup.make_content_hash(record)
                            change_status = await _dedup.record_digest(
                                template.name, rid, content_hash
                            )
                            if change_status and change_status != "changed":
                                logger.debug("Skipping unchanged record: %s", rid)
                                continue
                        new_records.append(record)
                    skipped = len(records) - len(new_records)
                    if skipped > 0:
                        logger.info("Dedup: skipped %d of %d records", skipped, len(records))
                    records = new_records

                if records:
                    await self._save_page_records(template, records, result)

                all_records.extend(records)

                await self._save_checkpoint(template.name, {
                    "status": "running",
                    "page": current_page,
                    "records_saved": len(result.records),
                    "template": template.name,
                })

                logger.info(
                    "Page %d: found %d records (total: %d)",
                    current_page,
                    len(records),
                    len(all_records),
                )

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

            except Exception as e:
                logger.error("Failed to crawl list page %d: %s", current_page, e)
                result.errors.append(f"List page {current_page}: {e}")
                break

        return all_records

    async def _crawl_list_pages_json(
        self, template: SiteTemplate, result: CrawlResult,
        resume_page: int | None = None,
    ) -> list[dict[str, Any]]:
        all_records: list[dict[str, Any]] = []
        start_page = resume_page if resume_page is not None else (
            template.list_pagination.start_page if template.list_pagination else 1
        )
        config_max_pages = template.list_pagination.max_pages if template.list_pagination else 1000
        results_per_page = template.list_pagination.results_per_page if template.list_pagination else 100

        item_path = template.json_item_path or ""

        effective_max_pages = config_max_pages or 10000
        current_page = start_page

        adapter_name = template.adapter

        adapter = get_adapter(adapter_name, template.base_url, self._client)

        await adapter.on_before_crawl(template)

        while 1:
            is_first = (current_page == start_page)
            page_succeeded = False

            for attempt in self._retry_loop():
                try:
                    await adapter.on_before_page(current_page, is_first)

                    url = template.get_full_list_url(current_page, num=results_per_page, peid=adapter.session.eid)
                    # url = template.get_full_list_url(current_page, num=results_per_page)

                    extra_headers = adapter.on_request_headers(current_page)
                    # extra_headers = {}
                    list_request = template.list_request.model_copy(update={
                        "headers": {**template.list_request.headers, **extra_headers}
                    }) if extra_headers else template.list_request

                    text = await self._client.request_page(url, list_request)
                    json_data = json.loads(text)

                    records = self._parser.parse_list_json(
                        json_data, item_path, template.list_fields
                    )

                    records = await adapter.on_after_page(current_page, records)

                    total_count = ""
                    if template.json_total_path:
                        total_val = resolve_json_path(json_data, template.json_total_path)
                        if total_val is not None:
                            total_count = f" / total={total_val}"
                            if current_page == start_page:
                                try:
                                    total = int(total_val)
                                    dynamic_pages = (
                                        total + results_per_page
                                    ) // results_per_page

                                    if template.json_total_num_pages:
                                        api_pages_val = resolve_json_path(
                                            json_data, template.json_total_num_pages
                                        )
                                        if api_pages_val is not None:
                                            try:
                                                api_pages = int(api_pages_val)
                                                if api_pages < dynamic_pages:
                                                    logger.info(
                                                        "API limits pages to %d (from %s=%d) "
                                                        "instead of calculated %d (total=%d / per_page=%d)",
                                                        api_pages,
                                                        template.json_total_num_pages,
                                                        api_pages,
                                                        dynamic_pages,
                                                        total,
                                                        results_per_page,
                                                    )
                                                    dynamic_pages = api_pages
                                            except (ValueError, TypeError):
                                                pass

                                    effective_max_pages = min(
                                        config_max_pages or 10000, dynamic_pages
                                    )
                                    logger.info(
                                        "Dynamic pagination: total=%d, per_page=%d, "
                                        "need %d pages (capped at %d)",
                                        total,
                                        results_per_page,
                                        dynamic_pages,
                                        effective_max_pages,
                                    )
                                except (ValueError, TypeError):
                                    pass

                    all_records.extend(records)

                    if records:
                        await self._save_page_records(template, records, result)

                    await self._save_checkpoint(template.name, {
                        "status": "running",
                        "page": current_page,
                        "records_saved": len(result.records),
                        "template": template.name,
                    })

                    logger.info(
                        "Page %d/%s: found %d records%s (cumulative: %d)",
                        current_page + 1,
                        dynamic_pages,
                        len(records),
                        total_count,
                        len(all_records),
                    )

                    page_succeeded = True
                    break

                except json.JSONDecodeError as e:
                    await self._client.mark_last_proxy_failed()

                except Exception as e:
                    adapter_action = await adapter.on_error(e, current_page, attempt)
                    if adapter_action == "abort":
                        result.errors.append(f"List page {current_page}: {e}")
                        await self._save_checkpoint(template.name, {
                            "status": "failed",
                            "page": current_page,
                            "records_saved": len(result.records),
                            "template": template.name,
                            "error": str(e),
                        })
                        return all_records
                    elif adapter_action == "reset_session":
                        await adapter.on_before_crawl(template)
                        continue
                    elif adapter_action == "skip":
                        break

            if not page_succeeded:
                logger.error(
                    "Page %d failed after many attempts, skipping",
                    current_page,
                )
                result.errors.append(f"List page {current_page}: exceeded retries")

            if not template.list_pagination:
                break

            adapter.on_page_advance()
            current_page += 1

            if current_page >= start_page + dynamic_pages:
                break

        await adapter.close()
        return all_records

    @staticmethod
    def _retry_loop():
        return itertools.count()

    async def _crawl_detail_pages(
        self,
        template: SiteTemplate,
        list_records: list[dict[str, Any]],
        result: CrawlResult,
    ) -> list[dict[str, Any]]:
        if template.response_type == ResponseType.JSON:
            return await self._crawl_detail_pages_json(template, list_records, result)
        return await self._crawl_detail_pages_html(template, list_records, result)

    async def _crawl_detail_pages_html(
        self,
        template: SiteTemplate,
        list_records: list[dict[str, Any]],
        result: CrawlResult,
    ) -> list[dict[str, Any]]:
        detail_urls = await self._resolve_detail_urls(template, list_records, result)

        tasks = [
            self._crawl_single_detail(template, url, base_record)
            for url, base_record in detail_urls
        ]

        detail_records: list[dict[str, Any]] = []
        batch_size = settings.max_concurrent_tasks
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i : i + batch_size]
            batch_results = await asyncio.gather(*batch, return_exceptions=True)
            batch_saved: list[dict[str, Any]] = []
            for item in batch_results:
                if isinstance(item, Exception):
                    logger.error("Detail page error: %s", item)
                    result.errors.append(str(item))
                elif item is not None:
                    detail_records.append(item)
                    batch_saved.append(item)
            if batch_saved:
                await self._save_page_records(template, batch_saved, result)

        return detail_records

    async def _crawl_detail_pages_json(
        self,
        template: SiteTemplate,
        list_records: list[dict[str, Any]],
        result: CrawlResult,
    ) -> list[dict[str, Any]]:
        detail_urls = await self._resolve_detail_urls(template, list_records, result)

        tasks = [
            self._crawl_single_detail_json(template, url, base_record)
            for url, base_record in detail_urls
        ]

        detail_records: list[dict[str, Any]] = []
        batch_size = settings.max_concurrent_tasks
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i : i + batch_size]
            batch_results = await asyncio.gather(*batch, return_exceptions=True)
            batch_saved: list[dict[str, Any]] = []
            for item in batch_results:
                if isinstance(item, Exception):
                    logger.error("Detail page JSON error: %s", item)
                    result.errors.append(str(item))
                elif item is not None:
                    detail_records.append(item)
                    batch_saved.append(item)
            if batch_saved:
                await self._save_page_records(template, batch_saved, result)

        return detail_records

    async def _resolve_detail_urls(
        self,
        template: SiteTemplate,
        list_records: list[dict[str, Any]],
        result: CrawlResult,
    ) -> list[tuple[str, dict[str, Any]]]:
        urls: list[tuple[str, dict[str, Any]]] = []

        if template.detail_url_selector:
            first_list_url = template.get_full_list_url(1)
            html = await self._client.request_page(first_list_url, template.list_request)
            links = self._parser.extract_links(
                html,
                template.detail_url_selector,
                template.detail_url_selector_type,
            )
            for link in links:
                full_url = template.get_full_detail_url(link)
                urls.append((full_url, {}))
        elif template.detail_page:
            for record in list_records:
                detail_path = template.detail_page
                for key, value in record.items():
                    placeholder = "{" + key + "}"
                    if placeholder in detail_path:
                        detail_path = detail_path.replace(placeholder, str(value))
                full_url = template.get_full_detail_url(detail_path)
                urls.append((full_url, record))
        else:
            for record in list_records:
                urls.append(("", record))

        return urls

    async def _crawl_single_detail(
        self,
        template: SiteTemplate,
        url: str,
        base_record: dict[str, Any],
    ) -> dict[str, Any] | None:
        async with self._semaphore:
            try:
                if not url:
                    return base_record

                html = await self._client.request_page(url, template.detail_request)
                detail = self._parser.parse_detail(html, template.detail_fields)
                merged = {**base_record, **detail, "_source_url": url}
                return merged

            except Exception as e:
                logger.error("Failed to crawl detail page %s: %s", url, e)
                raise

    async def _crawl_single_detail_json(
        self,
        template: SiteTemplate,
        url: str,
        base_record: dict[str, Any],
    ) -> dict[str, Any] | None:
        async with self._semaphore:
            try:
                if not url:
                    return base_record

                text = await self._client.request_page(url, template.detail_request)
                json_data = json.loads(text)
                detail = self._parser.parse_detail_json(json_data, template.detail_fields)
                merged = {**base_record, **detail, "_source_url": url}
                return merged

            except Exception as e:
                logger.error("Failed to crawl detail JSON %s: %s", url, e)
                raise

    async def close(self) -> None:
        await self._client.close()
        if hasattr(self._storage, "close"):
            await self._storage.close()
