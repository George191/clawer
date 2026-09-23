"""下载 Worker — 独立监听 MongoDB，下载资源文件并上传至 MinIO。

工作流程
--------
1. 轮询 MongoDB 中 `download_status=pending` 的记录
2. 根据模板的 download 配置提取下载链接
3. 使用内存中转（download_bytes + upload_bytes）存入 MinIO，无需落盘
4. 更新 MongoDB 记录的文件路径和下载状态

设计原则
--------
- 采集与下载完全解耦：本 Worker 独立于 SpiderEngine 运行
- 内存中转：无需落盘，但单个资源会占用等量内存
- 幂等性：通过 MongoDB 状态字段保证重复处理安全
- 模板驱动：根据 YAML 模板中的 download 配置自动选择下载策略
  支持 JSON 路径提取（如 Google Patents）和 CSS 选择器提取（如 Sealagom PDF）
"""

from __future__ import annotations

import asyncio
import mimetypes
import copy
import re
import time
from urllib.parse import unquote
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.base.http import DownloadResponse, HttpClient
from app.base.minio import MinioClient
from app.base.mongo import MongoClient
from app.config.settings import settings
from app.engine.template_loader import TemplateLoader
from app.logger import get_logger
from app.models.template import SiteTemplate
from app.utils.path import get_nested_value
from app.web.services.ai_collect_store import ai_collect_store

logger = get_logger(__name__)

# ══════════════════════════════════════════════════════════════════════
#  重试配置
# ══════════════════════════════════════════════════════════════════════
# 初始重试延迟（秒），指数退避起始值
RETRY_INITIAL_DELAY: float = 1.0
# 最大重试延迟（秒），退避上限以避免请求风暴
RETRY_MAX_DELAY: float = 60.0
RETRY_MAX_ATTEMPTS: int = 5
# 告警阈值：每 N 次连续重试输出 WARNING 日志
RETRY_ALERT_THRESHOLD: int = 10
# 严重告警阈值：每 N 次连续重试输出 ERROR 日志，提示持续故障
RETRY_CRITICAL_THRESHOLD: int = 50
# 403 可能是代理/上游临时拒绝，但长期 403 会占住下载 worker。
RETRY_MAX_FORBIDDEN_ATTEMPTS: int = 5
FORBIDDEN_ASSET_SKIPPED = "__forbidden_asset_skipped__"
NOT_FOUND_ASSET = "__not_found_asset__"
NOT_FOUND_ASSET_MARKER = {
    "url": "",
    "description": "Source file returned HTTP 404; the file no longer exists.",
    "status_code": 404,
}

# Some released templates use a business-specific Mongo collection name.
# Keep the template name unchanged for loading its download configuration,
# while routing the downloader query to the actual collection.
DOWNLOAD_COLLECTION_OVERRIDES = {
    "sec_edgar": "sec_edgar_filing",
}
DOWNLOAD_TEMPLATE_OVERRIDES = {
    collection: template for template, collection in DOWNLOAD_COLLECTION_OVERRIDES.items()
}


@dataclass(slots=True)
class AssetDownloadJob:
    dl_info: dict[str, Any]
    template_name: str
    data_type: str
    record_id: str
    use_proxy: bool
    result: asyncio.Future[tuple[dict[str, Any], "AssetResult"]]


@dataclass(frozen=True, slots=True)
class AssetResult:
    kind: str
    source_url: str
    final_url: str = ""
    content_type: str = ""
    asset_path: str | None = None


class DownloadWorker:
    """通用下载 Worker — 模板驱动，支持任意类型的资源下载。

    全库扫描机制：
        - 启动时枚举所有 MongoDB 集合，调用 get_collection_stats() 输出概览
        - 每轮 _process_batch 通过 get_pending_downloads(balanced=True)
          均衡轮询各集合，避免单集合独占批次
        - 每个记录携带 _meta.template，Worker 据此动态加载对应模板的 download 配置
        - 无 download 配置的模板自动标记 no_assets 并缓存，后续扫描跳过

    模板 download 配置字段：
        selector:          下载链接选择器或 JSON 路径
        selector_type:     json / css / xpath
        link_type:         href / src / text
        file_extension:    强制文件扩展名（可选）
        filename_selector: 文件名选择器（可选）
        url_prefix:        下载 URL 前缀（可选）
    """

    def __init__(
        self,
        poll_interval: int = 10,
        batch_size: int = 50,
        template_name: str | None = None,
    ) -> None:
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._template_name = template_name
        self._http: HttpClient | None = None
        self._minio: MinioClient | None = None
        self._mongo = MongoClient()
        self._semaphore: asyncio.Semaphore | None = None
        self._asset_queue: asyncio.Queue[AssetDownloadJob] | None = None
        self._asset_workers: list[asyncio.Task[None]] = []
        self._running = False
        self._template_loader = TemplateLoader()
        # 模板缓存：避免每次下载都重新加载 YAML
        self._template_cache: dict[str, SiteTemplate] = {}
        # 无下载需求模板缓存：避免重复加载无 download 配置的模板
        self._no_assets_templates: set[str] = set()

    @property
    def _query_template_name(self) -> str | None:
        """Return the collection selector used by Mongo download queries."""
        if self._template_name is None:
            return None
        return DOWNLOAD_COLLECTION_OVERRIDES.get(
            self._template_name, self._template_name,
        )

    async def run(self) -> None:
        self._running = True
        self._http = HttpClient()
        self._minio = MinioClient()
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)
        self._start_asset_workers()

        logger.info(
            "DownloadWorker started (poll=%ds, batch=%d, record_concurrency=%d, "
            "asset_concurrency=%d, download_proxy=%s, template=%s)",
            self._poll_interval,
            self._batch_size,
            settings.max_concurrent_tasks,
            settings.download_asset_concurrency,
            "on" if settings.download_use_proxy else "off",
            self._template_name or "ALL",
        )

        while self._running:
            try:
                count = await self._process_batch()
                if count == 0:
                    await self._log_startup_stats()
                    await asyncio.sleep(self._poll_interval)
            except ValueError:
                raise
            except Exception:
                logger.exception("DownloadWorker loop error")
                await asyncio.sleep(self._poll_interval)

    async def _process_batch(self) -> int:
        pending = await self._mongo.get_pending_downloads(
            template_name=self._query_template_name,
            limit=self._batch_size,
        )
        if not pending:
            return 0

        logger.info("DownloadWorker: found %d pending downloads", len(pending))
        tasks = [self._download_one(rec) for rec in pending]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        success = sum(1 for r in results if r is True)
        logger.info("DownloadWorker: completed %d/%d records", success, len(pending))
        return success

    async def _download_one(self, record: dict[str, Any]) -> bool:
        """处理单条记录的资源下载。

        根据模板的 download 配置决定下载策略：
        - 无 download 配置 → 标记 no_assets
        - JSON 路径提取 → 从 record 中按路径取值
        - CSS 选择器提取 → 请求页面后解析（未来扩展）
        """
        meta = record.get("_meta", {})
        record_id = meta.get("record_id", "")
        collection_name = meta.get("template", "")
        template_name = DOWNLOAD_TEMPLATE_OVERRIDES.get(collection_name, collection_name)
        workspace_task_id = str(
            (meta.get("search_params") or {}).get("__workspace_task_id") or ""
        )

        if not record_id or not template_name:
            logger.warning("DownloadWorker: skip record with missing meta")
            return False

        if workspace_task_id:
            task_control = await ai_collect_store.get_task_control(workspace_task_id)
            if task_control is None:
                workspace_task_id = ""
            elif task_control.get("download_state") != "running":
                await self._mongo.update_file_status(collection_name, record_id, "pending")
                return False

        async with self._semaphore:
            try:
                # 快速路径：模板已知无下载需求，直接标记
                if template_name in self._no_assets_templates:
                    await self._mongo.update_file_status(
                        collection_name, record_id, "no_assets",
                    )
                    return True

                # 加载模板
                template = await self._get_template(template_name)
                if template is None:
                    await self._mongo.update_file_status(
                        collection_name, record_id, "no_assets",
                    )
                    return True

                download_configs = template.download
                data_type = str(meta.get("data_type") or template.data_type).lower()
                if not download_configs and data_type != "news":
                    # 缓存该模板，后续扫描跳过
                    self._no_assets_templates.add(template_name)
                    logger.info(
                        "DownloadWorker: template '%s' has no download config, "
                        "caching as no_assets",
                        template_name,
                    )
                    await self._mongo.update_file_status(
                        collection_name, record_id, "no_assets",
                    )
                    return True

                # 新闻正文的所有 a[href] 都先存 external_links，下载时再通过
                # Content-Type 将文件资源迁移为附件。
                download_urls = self._extract_record_download_urls(
                    record, download_configs, template_name, data_type,
                )

                if not download_urls:
                    await self._mongo.update_file_status(
                        collection_name, record_id, "no_assets",
                    )
                    return True

                # 下载并上传到 MinIO
                pending_by_url: dict[str, dict[str, Any]] = {}
                skipped_existing = 0
                expected_asset_keys = set()

                for idx, dl_info in enumerate(download_urls):
                    asset_key = dl_info.get("asset_key", f"assets.{idx}")
                    expected_asset_keys.add(asset_key)
                    is_attachment_candidate = dl_info.get("source_field") in {
                        "attachments",
                        "external_links",
                    }
                    if self._asset_exists(record, asset_key) and not is_attachment_candidate:
                        skipped_existing += 1
                        continue

                    url = str(dl_info.get("url") or "").strip()
                    if not url:
                        continue

                    pending = pending_by_url.setdefault(
                        url,
                        {
                            "url": url,
                            "filename": dl_info["filename"],
                            "asset_keys": [],
                            "is_attachment_candidate": False,
                            "candidate_type": "",
                        },
                    )
                    pending["asset_keys"].append(asset_key)
                    pending["is_attachment_candidate"] = (
                        pending["is_attachment_candidate"] or is_attachment_candidate
                    )
                    if dl_info.get("candidate_type"):
                        pending["candidate_type"] = dl_info["candidate_type"]

                # The record may already have partial assets from a previous run.
                if not pending_by_url:
                    status = "downloaded" if skipped_existing else "no_assets"
                    await self._mongo.update_file_status(
                        collection_name, record_id, status,
                    )
                    logger.info(
                        "DownloadWorker: %s has %d existing assets, status=%s",
                        record_id, skipped_existing, status,
                    )
                    return True

                asset_results = await asyncio.gather(*(
                    self._download_pending_asset(
                        dl_info,
                        template_name,
                        data_type,
                        record_id,
                        template.effective_download_use_proxy,
                    )
                    for dl_info in pending_by_url.values()
                ))

                updates: dict[str, str] = {}
                not_found_updates: dict[str, dict[str, Any]] = {}
                failed_assets = 0
                external_asset_keys: set[str] = set()
                external_urls: list[str] = []
                for dl_info, result in asset_results:
                    asset_keys = list(dict.fromkeys(dl_info["asset_keys"]))
                    if result.kind == "skipped":
                        failed_assets += 1
                        continue
                    if result.kind == "external_link":
                        external_asset_keys.update(asset_keys)
                        external_urls.append(result.source_url)
                        continue
                    if result.kind == "not_found":
                        not_found_updates.update(
                            (key, dict(NOT_FOUND_ASSET_MARKER)) for key in asset_keys
                        )
                        continue
                    if result.kind != "file" or not result.asset_path:
                        failed_assets += 1
                        continue
                    updates.update(dict.fromkeys(asset_keys, result.asset_path))

                downloaded_assets = len(updates)
                existing_or_downloaded_assets = {
                    asset_key
                    for asset_key in expected_asset_keys
                    if (
                        asset_key in updates
                        or asset_key in not_found_updates
                        or asset_key in external_asset_keys
                        or self._asset_exists(record, asset_key)
                    )
                }
                missing_asset_keys = expected_asset_keys - existing_or_downloaded_assets

                if not self._running:
                    final_status = "pending"
                elif failed_assets or missing_asset_keys:
                    final_status = "failed"
                elif downloaded_assets or not_found_updates or skipped_existing:
                    final_status = "downloaded"
                else:
                    final_status = "no_assets"

                if data_type == "news":
                    record_updates, unset_fields = self._build_news_download_updates(
                        record,
                        download_urls,
                        updates,
                        external_urls,
                        not_found_updates=not_found_updates,
                        external_asset_keys=external_asset_keys,
                    )
                else:
                    record_updates = {**updates, **not_found_updates}
                    unset_fields = set()
                if data_type != "news" and (downloaded_assets or not_found_updates):
                    record_updates["_meta.sync_status"] = "pending"
                if not_found_updates:
                    record_updates["_meta.has_not_found_assets"] = True

                empty_asset_fields = self._empty_asset_fields(
                    record, record_updates,
                )
                if "assets" in empty_asset_fields:
                    record_updates = {
                        key: value for key, value in record_updates.items()
                        if key != "assets" and not key.startswith("assets.")
                    }
                else:
                    for field in empty_asset_fields:
                        record_updates.pop(field, None)

                mongo_started_at = time.perf_counter()
                await self._mongo.update_download_result(
                    collection_name,
                    record_id,
                    record_updates,
                    final_status,
                    unset_fields=empty_asset_fields | unset_fields,
                )
                logger.debug(
                    "DownloadWorker timing: phase=mongo record=%s fields=%d "
                    "seconds=%.3f",
                    record_id,
                    len(updates),
                    time.perf_counter() - mongo_started_at,
                )
                for key, asset_path in updates.items():
                    self._set_nested_value(record, key, asset_path)

                if not self._running:
                    logger.info(
                        "DownloadWorker: worker stopping, leaving %s pending",
                        record_id,
                    )
                    return False

                if failed_assets:
                    logger.warning(
                        "DownloadWorker: %s has %d failed assets "
                        "(downloaded=%d, skipped_existing=%d)",
                        record_id, failed_assets, downloaded_assets, skipped_existing,
                    )
                    return False
                if missing_asset_keys:
                    logger.warning(
                        "DownloadWorker: %s missing %d expected asset fields",
                        record_id, len(missing_asset_keys),
                    )
                    return False
                if downloaded_assets or not_found_updates or skipped_existing:
                    logger.info(
                        "DownloadWorker: downloaded %d assets for %s "
                        "(skipped_existing=%d)",
                        downloaded_assets,
                        record_id,
                        skipped_existing,
                    )
                    if workspace_task_id and downloaded_assets:
                        await ai_collect_store.increment_task_stats(
                            workspace_task_id,
                            downloaded=downloaded_assets,
                        )
                        await ai_collect_store.append_task_log(
                            workspace_task_id,
                            "ok",
                            f"下载完成：record={record_id}, assets={downloaded_assets}",
                        )
                return True

            except Exception:
                logger.exception("DownloadWorker: failed for %s", record_id)
                try:
                    await self._mongo.update_file_status(
                        collection_name, record_id, "failed",
                    )
                except Exception:
                    pass
                return False

    @staticmethod
    def _asset_exists(record: dict[str, Any], asset_key: str) -> bool:
        value = get_nested_value(record, asset_key)
        if isinstance(value, str):
            if value.strip():
                return True
            parent_path = asset_key.rsplit(".", 1)[0]
            parent = get_nested_value(record, parent_path)
            return isinstance(parent, dict) and parent.get("status_code") == 404
        return value is not None

    @staticmethod
    def _set_nested_value(record: dict[str, Any], asset_key: str, value: str) -> None:
        current: dict[str, Any] = record
        parts = asset_key.split(".")
        for part in parts[:-1]:
            next_value = current.get(part)
            if not isinstance(next_value, dict):
                next_value = {}
                current[part] = next_value
            current = next_value
        current[parts[-1]] = value

    @classmethod
    def _empty_asset_fields(
        cls,
        record: dict[str, Any],
        updates: dict[str, Any],
    ) -> set[str]:
        """Return empty asset containers that should be removed from MongoDB."""
        existing_assets = record.get("assets")
        projected: dict[str, Any] = {
            "assets": copy.deepcopy(existing_assets)
            if isinstance(existing_assets, dict)
            else {},
        }
        touched = isinstance(existing_assets, dict)
        for key, value in updates.items():
            if key == "assets":
                projected["assets"] = copy.deepcopy(value)
                touched = True
            elif key.startswith("assets."):
                cls._set_nested_value(projected, key, copy.deepcopy(value))
                touched = True

        assets = projected.get("assets")
        if not touched or not isinstance(assets, dict):
            return set()

        empty_children = {
            f"assets.{key}"
            for key, value in assets.items()
            if isinstance(value, (dict, list)) and not value
        }
        nonempty_children = {
            key: value
            for key, value in assets.items()
            if f"assets.{key}" not in empty_children
        }
        return empty_children if nonempty_children else {"assets"}

    async def _get_template(self, template_name: str) -> SiteTemplate | None:
        """获取模板（带缓存）。"""
        if template_name in self._template_cache:
            return self._template_cache[template_name]

        try:
            released = await self._template_loader.load_released(
                template_name,
                validate_params=False,
                load_adapter=False,
            )
            template = released.template
            self._template_cache[template_name] = template
            return template
        except FileNotFoundError:
            logger.warning(
                "DownloadWorker: template '%s' not found, skipping download",
                template_name,
            )
            return None
        except Exception:
            logger.exception(
                "DownloadWorker: failed to load template '%s'",
                template_name,
            )
            return None

    async def _log_startup_stats(self) -> None:
        """启动时输出所有集合的下载状态概览。"""
        try:
            stats = await self._mongo.get_collection_stats(self._query_template_name)
            if not stats:
                return

            total_pending = sum(s["pending_download"] for s in stats)
            logger.info(
                "DownloadWorker: scanning %d collections, "
                "%d records pending download",
                len(stats), total_pending,
            )
            for s in stats:
                logger.info(
                    "  [%s] total=%d pending=%d downloaded=%d "
                    "no_assets=%d failed=%d",
                    s["name"], s["total"], s["pending_download"],
                    s["downloaded"], s["no_assets"], s["failed"],
                )
        except ValueError:
            raise
        except Exception:
            logger.warning("DownloadWorker: failed to get collection stats")

    def _extract_record_download_urls(
        self,
        record: dict[str, Any],
        download_configs: list[Any],
        template_name: str,
        data_type: str,
    ) -> list[dict[str, Any]]:
        """Return the resources to download for one record."""
        if data_type == "news":
            return self._extract_news_download_urls(record)

        urls: list[dict[str, Any]] = []
        for download_config in download_configs:
            urls.extend(self._extract_download_urls(
                record, download_config, template_name,
            ))
        return urls

    def _extract_news_download_urls(
        self,
        record: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return existing attachments and external links for MIME classification.

        Existing attachments must be downloaded by their original placeholder
        index.  News records can contain an ``<a>`` wrapper around an ``<img>``;
        the image and the linked original are intentionally separate assets.
        """
        urls: list[dict[str, Any]] = []
        attachments = record.get("attachments")
        if isinstance(attachments, list):
            for fallback, item in enumerate(attachments):
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                match = re.fullmatch(
                    r"\{\{attachment_(\d+)\}\}",
                    str(item.get("placeholder") or ""),
                )
                index = match.group(1) if match else str(fallback)
                urls.append({
                    "url": url,
                    "filename": self._make_filename(url, suffix=f"_{index.zfill(5)}"),
                    "asset_key": f"assets.attachments.{index}.url",
                    "source_field": "attachments",
                    "candidate_type": str(item.get("type") or "").lower(),
                })

        external_links = record.get("external_links")
        if not isinstance(external_links, list):
            return urls

        for index, url in enumerate(external_links):
            if not isinstance(url, str) or not url.strip():
                continue
            clean_url = url.strip()
            urls.append({
                "url": clean_url,
                "filename": self._make_filename(
                    clean_url, suffix=f"_{index:05d}",
                ),
                "asset_key": f"assets.external_links.{index}",
                "source_field": "external_links",
            })
        return urls

    def _extract_download_urls(
        self,
        record: dict[str, Any],
        download_config: Any,
        template_name: str,
    ) -> list[dict[str, Any]]:
        """从记录中提取下载 URL 列表。

        支持两种模式：
        1. JSON 路径模式：selector 为 JSON 路径，从 record 中取值
           - 如果路径指向单个值 → 返回单条 URL
           - 如果路径指向 list → 展开为多条 URL（如 Google Patents 的 figures）
        2. 多选择器模式：未来支持从页面中提取多个 URL

        Returns:
            下载信息列表，每项包含 url, filename, asset_key
        """
        urls: list[dict[str, Any]] = []

        selector = download_config.selector
        selector_type = download_config.selector_type

        if selector_type == "json":
            urls = self._extract_json_urls(record, download_config)
        else:
            logger.warning(
                "DownloadWorker: unsupported selector_type '%s' for '%s'",
                selector_type, template_name,
            )

        for item in urls:
            item.setdefault("selector", download_config.selector)

        return urls

    @staticmethod
    def _build_news_download_updates(
        record: dict[str, Any],
        download_urls: list[dict[str, Any]],
        downloaded_assets: dict[str, str],
        external_urls: list[str],
        *,
        not_found_updates: dict[str, dict[str, Any]] | None = None,
        external_asset_keys: set[str] | None = None,
    ) -> tuple[dict[str, Any], set[str]]:
        """Persist existing attachment assets and migrate file-like external links."""
        not_found_updates = not_found_updates or {}
        external_asset_keys = external_asset_keys or set()
        key_to_url = {
            str(item.get("asset_key")): str(item.get("url") or "")
            for item in download_urls
        }
        key_to_info = {
            str(item.get("asset_key")): item for item in download_urls
        }
        attachments = [
            item for item in (record.get("attachments") or [])
            if isinstance(item, dict)
        ]
        attachment_assets = dict(
            ((record.get("assets") or {}).get("attachments") or {})
        )
        attachment_index_by_url: dict[str, str] = {}
        for fallback, item in enumerate(attachments):
            if not isinstance(item, dict) or not item.get("url"):
                continue
            match = re.fullmatch(
                r"\{\{attachment_(\d+)\}\}",
                str(item.get("placeholder") or ""),
            )
            attachment_index_by_url[str(item["url"])] = (
                match.group(1) if match else str(fallback)
            )
        converted_urls: set[str] = set()
        content_html = str(record.get("content_html") or "")
        attachment_changed = False

        for asset_key, asset_path in downloaded_assets.items():
            item = key_to_info.get(asset_key) or {}
            if item.get("source_field") != "attachments":
                continue
            match = re.fullmatch(r"assets\.attachments\.([^.]+)\.url", asset_key)
            if match:
                attachment_assets[match.group(1)] = {"url": asset_path}
                attachment_changed = True

        for asset_key, marker in not_found_updates.items():
            item = key_to_info.get(asset_key) or {}
            if item.get("source_field") != "attachments":
                continue
            match = re.fullmatch(r"assets\.attachments\.([^.]+)\.url", asset_key)
            if match:
                attachment_assets[match.group(1)] = dict(marker)
                attachment_changed = True

        removed_attachment_urls: set[str] = set()
        for asset_key in external_asset_keys:
            item = key_to_info.get(asset_key) or {}
            if item.get("source_field") != "attachments":
                continue
            source_url = key_to_url.get(asset_key, "")
            if not source_url:
                continue
            removed_attachment_urls.add(source_url)
            placeholder = ""
            for attachment in attachments:
                if isinstance(attachment, dict) and attachment.get("url") == source_url:
                    placeholder = str(attachment.get("placeholder") or "")
                    break
            if placeholder:
                content_html = content_html.replace(placeholder, source_url)
            match = re.fullmatch(r"assets\.attachments\.([^.]+)\.url", asset_key)
            if match:
                attachment_assets.pop(match.group(1), None)
                attachment_changed = True

        if removed_attachment_urls:
            attachment_changed = True
            attachments = [
                item for item in attachments
                if str(item.get("url") or "") not in removed_attachment_urls
            ]

        for asset_key, asset_path in downloaded_assets.items():
            source_url = key_to_url.get(asset_key, "")
            if not source_url:
                continue
            if (key_to_info.get(asset_key) or {}).get("source_field") == "attachments":
                continue
            existing_index = attachment_index_by_url.get(source_url)
            if existing_index is not None:
                attachment_assets[existing_index] = {"url": asset_path}
                attachment_changed = True
                continue
            index = len(attachments)
            placeholder = f"{{{{attachment_{index}}}}}"
            attachments.append({
                "url": source_url,
                "placeholder": placeholder,
                "type": "file",
            })
            attachment_assets[str(index)] = {"url": asset_path}
            content_html = content_html.replace(source_url, placeholder)
            converted_urls.add(source_url)

        remaining_external = [
            url for url in (record.get("external_links") or [])
            if isinstance(url, str) and url not in converted_urls
        ]
        remaining_external.extend(
            url for url in external_urls if url not in remaining_external
        )
        updates: dict[str, Any] = {"_meta.sync_status": "pending"}
        unset_fields: set[str] = set()
        if converted_urls or attachment_changed:
            updates["attachments"] = attachments
            updates["assets.attachments"] = attachment_assets
            updates["content_html"] = content_html
        if remaining_external:
            updates["external_links"] = list(dict.fromkeys(remaining_external))
        else:
            unset_fields.add("external_links")
        return updates, unset_fields

    def _extract_json_urls(
        self,
        record: dict[str, Any],
        download_config: Any,
    ) -> list[dict[str, Any]]:
        """从 JSON 记录中提取下载 URL。

        支持嵌套路径，如 'patent.pdf' → record['patent']['pdf']
        支持列表展开，如 'patent.figures' → 遍历列表中的每项

        asset_key 规则：
        - 单值: assets.{selector}，如 assets.patent.pdf
        - 列表: assets.{selector}.{index}，如 assets.patent.figures.0
        """
        selector = download_config.selector
        url_prefix = getattr(download_config, 'url_prefix', None) or ""
        file_ext = getattr(download_config, 'file_extension', None)

        # 提取原始值
        raw_value = get_nested_value(record, selector)
        if raw_value is None:
            logger.debug("DownloadWorker: no value at path '%s'", selector)
            return []

        # 如果是列表，展开处理
        if isinstance(raw_value, list):
            urls = []
            for i, item in enumerate(raw_value):
                if isinstance(item, dict):
                    resource_index = i
                    # 复合对象：提取各字段 URL，保留字段名以区分
                    field_urls = self._extract_url_from_dict(
                        item, url_prefix,
                    )
                    for field_name, sub_url in field_urls:
                        filename = self._make_filename(
                            sub_url, file_ext, suffix=f"_{resource_index:05d}",
                        )
                        urls.append({
                            "url": sub_url,
                            "filename": filename,
                            "asset_key": f"assets.{selector}.{resource_index}.{field_name}",
                            "candidate_type": str(item.get("type") or "").lower(),
                        })
                elif isinstance(item, str):
                    full_url = url_prefix + item if url_prefix else item
                    filename = self._make_filename(
                        full_url, file_ext, suffix=f"_{i:05d}",
                    )
                    urls.append({
                        "url": full_url,
                        "filename": filename,
                        "asset_key": f"assets.{selector}.{i}",
                    })
            return urls

        # 单值处理
        if isinstance(raw_value, dict):
            # 复合对象：提取各字段 URL，保留字段名以区分
            field_urls = self._extract_url_from_dict(
                raw_value, url_prefix,
            )
            urls: list[dict[str, Any]] = []
            for field_name, sub_url in field_urls:
                urls.append({
                    "url": sub_url,
                    "filename": self._make_filename(sub_url, file_ext),
                    "asset_key": f"assets.{selector}.{field_name}",
                })
            return urls

        # 字符串值
        val = str(raw_value)
        full_url = url_prefix + val if not val.startswith("http") else val
        return [{
            "url": full_url,
            "filename": self._make_filename(full_url, file_ext),
            "asset_key": f"assets.{selector}",
        }]

    def _extract_url_from_dict(
        self,
        data: dict[str, Any],
        url_prefix: str,
    ) -> list[tuple[str, str]]:
        """从字典中提取 URL。按优先级尝试常见字段名。

        Returns:
            (field_name, url) 元组列表，保留字段名用于 asset_key 区分。
        """
        results: list[tuple[str, str]] = []
        for key in ("href", "src", "url", "link", "full", "thumbnail", "pdf"):
            if key in data and data[key]:
                val = str(data[key])
                url = url_prefix + val if url_prefix else val
                results.append((key, url))
        return results

    @staticmethod
    def _make_filename(
        url: str,
        file_ext: str | None = None,
        suffix: str = "",
    ) -> str:
        """从 URL 或扩展名生成文件名。"""
        basename = DownloadWorker._url_basename(url)
        if file_ext:
            ext = file_ext.lstrip(".")
        else:
            match = re.search(r"\.([a-z0-9]{1,16})$", basename.lower())
            ext = match.group(1) if match else "bin"

        name_part = basename.rsplit(".", 1)[0]
        if not name_part or len(name_part) > 60:
            # URL 最后一段不合适，用 hash
            import hashlib
            name_part = hashlib.md5(url.encode()).hexdigest()[:12]

        return f"{name_part}{suffix}.{ext}"

    @staticmethod
    def _url_basename(url: str) -> str:
        """Return the final URL path segment without query or fragment."""
        path_part = url.split("?", 1)[0].split("#", 1)[0]
        return path_part.rsplit("/", 1)[-1]

    @staticmethod
    def _content_disposition_filename(response: DownloadResponse) -> str:
        """Return a safe server-provided filename, if one was supplied."""
        header = next(
            (
                value for key, value in response.headers.items()
                if key.lower() == "content-disposition"
            ),
            "",
        )
        if not header:
            return ""

        match = re.search(r"filename\*=\s*[^']*''([^;]+)", header, re.IGNORECASE)
        if match:
            filename = unquote(match.group(1).strip().strip('"'))
        else:
            match = re.search(r"filename\s*=\s*(?:\"([^\"]+)\"|([^;]+))", header, re.IGNORECASE)
            filename = (match.group(1) or match.group(2)).strip() if match else ""
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        filename = re.sub(r"[^A-Za-z0-9._() -]", "_", filename).strip(" .")
        return filename[:120]

    @classmethod
    def _resolve_attachment_filename(
        cls,
        url: str,
        response: DownloadResponse,
        fallback: str,
    ) -> str:
        """Resolve a document candidate's name after receiving its response.

        Server metadata wins. If it is absent, the URL basename is preserved.
        The pre-download fallback is used only when the URL has no basename.
        """
        server_filename = cls._content_disposition_filename(response)
        if server_filename:
            return server_filename

        basename = cls._url_basename(url)
        if not basename:
            return fallback

        # Keep a real URL filename unchanged; append the response-derived
        # suffix only for extensionless or unknown-name endpoints.
        guessed_type, _ = mimetypes.guess_type(basename)
        if guessed_type:
            return basename
        content_ext = mimetypes.guess_extension(response.content_type) or ""
        return f"{basename}{content_ext}" if content_ext else basename

    @staticmethod
    def _extract_status_code(exc: Exception) -> int | None:
        """从异常中提取 HTTP 状态码。

        DownloadError 携带 status_code；curl_cffi 的 HTTPError 携带 response。
        网络异常（RequestsError 等）无状态码，返回 None。
        """
        status = getattr(exc, "status_code", None)
        if status is not None:
            return status
        response = getattr(exc, "response", None)
        if response is not None:
            return getattr(response, "status_code", None)
        return None

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        """判断异常是否可重试。

        - 403: Access Denied / 无权限 → 有上限重试
        - 404 / ValueError: 资源不存在或请求参数无效 → 不重试
        - 其他（5xx、网络超时、连接重置等）→ 可重试
        """
        if isinstance(exc, ValueError):
            return False
        status = DownloadWorker._extract_status_code(exc)
        return status != 404

    @staticmethod
    def _is_html_response(response: DownloadResponse) -> bool:
        # Trust the server-declared media type only.  Sniffing arbitrary
        # attachment bytes can misclassify binary resources as HTML.
        return response.content_type in {"text/html", "application/xhtml+xml"}

    @staticmethod
    def _is_invalid_external_html(response: DownloadResponse) -> bool:
        prefix = response.data[:32768].decode("utf-8", errors="ignore").lower()
        markers = (
            "cf-chl-",
            "challenge-platform",
            "cloudflare ray id",
            "window._cf_chl_opt",
            "window.awswafcookiedomainlist",
            "window.gokuprops",
            "captcha",
            "verify you are human",
            "checking your browser",
            "just a moment...",
            "zscaler directory authentication",
        )
        if any(marker in prefix for marker in markers):
            return True

        title_match = re.search(r"<title[^>]*>(.*?)</title>", prefix, re.DOTALL)
        title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
        error_titles = (
            "404",
            "not found",
            "access denied",
            "forbidden",
            "service unavailable",
            "internal server error",
            "bad gateway",
        )
        if any(marker in title for marker in error_titles):
            return True

        final_path = response.final_url.lower().split("?", 1)[0].rstrip("/")
        login_path = final_path.endswith(("/login", "/signin", "/sign-in", "/auth"))
        login_title = any(marker in title for marker in ("sign in", "signin", "log in", "login"))
        password_form = bool(re.search(r"type\s*=\s*['\"]password['\"]", prefix))
        return (login_path or login_title) and password_form

    async def _download_with_retry(
        self, url: str, *, use_proxy: bool
    ) -> DownloadResponse | str | None:
        """带有限重试的资源下载。

        策略：
        - 404 / ValueError → 立即返回 None，不重试
        - 其他错误（5xx、网络超时等）→ 最多重试 RETRY_MAX_ATTEMPTS 次
        - worker 停止时退出循环，返回 None

        每次重试独立下载，失败后字节即被回收，无内存泄漏。
        """
        retry_count = 0
        delay = RETRY_INITIAL_DELAY
        started_at = time.perf_counter()

        while self._running:
            try:
                response = await self._http.download_response(url, use_proxy=use_proxy)
                logger.debug(
                    "DownloadWorker timing: phase=http attempts=%d seconds=%.3f url=%s",
                    retry_count + 1,
                    time.perf_counter() - started_at,
                    url,
                )
                if retry_count > 0:
                    logger.info(
                        "DownloadWorker: succeeded after %d retries: %s",
                        retry_count, url,
                    )
                return response
            except Exception as exc:
                # 不可重试错误：403 / 404 / 文件过大
                if not self._is_retryable(exc):
                    logger.debug(
                        "DownloadWorker timing: phase=http attempts=%d seconds=%.3f "
                        "result=skipped url=%s",
                        retry_count + 1,
                        time.perf_counter() - started_at,
                        url,
                    )
                    status = self._extract_status_code(exc)
                    if status == 403:
                        logger.warning(
                            "DownloadWorker: 403 access denied, skipping: %s",
                            url,
                        )
                    elif status == 404:
                        logger.warning(
                            "DownloadWorker: 404 not found, skipping: %s",
                            url,
                        )
                        return NOT_FOUND_ASSET
                    else:
                        logger.warning(
                            "DownloadWorker: non-retryable error, skipping: %s (%s)",
                            url, exc,
                        )
                    return None

                # 可重试错误：记录并退避
                retry_count += 1
                error_type = type(exc).__name__
                status = self._extract_status_code(exc)
                timestamp = datetime.now(timezone.utc).isoformat()
                logger.warning(
                    "DownloadWorker: retry %d for %s | error=%s | status=%s | time=%s",
                    retry_count, url, error_type, status, timestamp,
                )

                if (
                    status == 403
                    and retry_count >= RETRY_MAX_FORBIDDEN_ATTEMPTS
                ):
                    logger.warning(
                        "DownloadWorker: reached max 403 retries (%d), skipping: %s",
                        RETRY_MAX_FORBIDDEN_ATTEMPTS,
                        url,
                    )
                    return FORBIDDEN_ASSET_SKIPPED

                if retry_count >= RETRY_MAX_ATTEMPTS:
                    logger.warning(
                        "DownloadWorker: reached max retries (%d), marking asset failed: %s",
                        RETRY_MAX_ATTEMPTS,
                        url,
                    )
                    return None

                # 阈值告警：连续重试达到预设阈值时触发通知
                if retry_count % RETRY_CRITICAL_THRESHOLD == 0:
                    logger.error(
                        "DownloadWorker: CRITICAL alert - %d consecutive retries "
                        "for %s (error=%s), possible persistent failure",
                        retry_count, url, error_type,
                    )
                elif retry_count % RETRY_ALERT_THRESHOLD == 0:
                    logger.warning(
                        "DownloadWorker: alert - %d consecutive retries for %s "
                        "(error=%s)",
                        retry_count, url, error_type,
                    )

                # 指数退避，上限 RETRY_MAX_DELAY 以避免请求风暴
                await asyncio.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_DELAY)

        # worker 已停止，退出重试循环
        logger.info(
            "DownloadWorker: worker stopping, aborting retries for %s "
            "(attempted %d retries)",
            url, retry_count,
        )
        return None

    async def _download_asset_to_minio(
        self,
        dl_info: dict[str, Any],
        template_name: str,
        data_type: str,
        record_id: str,
        use_proxy: bool,
    ) -> AssetResult:
        """下载单个资源文件并上传到 MinIO。

        下载阶段使用 _download_with_retry 实现有限重试；
        上传阶段失败不重试（MinIO 故障属基础设施问题，由上层处理）。
        """
        url = str(dl_info["url"])
        filename = str(dl_info["filename"])

        response = await self._download_with_retry(url, use_proxy=use_proxy)
        if response == FORBIDDEN_ASSET_SKIPPED:
            return AssetResult(kind="skipped", source_url=url)
        if response == NOT_FOUND_ASSET:
            return AssetResult(kind="not_found", source_url=url)
        if not isinstance(response, DownloadResponse):
            return AssetResult(kind="failed", source_url=url)
        if dl_info.get("is_attachment_candidate") and self._is_html_response(response):
            if self._is_invalid_external_html(response):
                return AssetResult(
                    kind="failed",
                    source_url=url,
                    final_url=response.final_url,
                    content_type=response.content_type,
                )
            return AssetResult(
                kind="external_link",
                source_url=url,
                final_url=response.final_url,
                content_type=response.content_type,
            )

        # The HTTP response is the sole MIME source for uploaded content.
        # Do not infer it from the URL or the generated filename.
        content_type = response.content_type or "application/octet-stream"
        if dl_info.get("is_attachment_candidate"):
            filename = self._resolve_attachment_filename(url, response, filename)

        retry_count = 0
        delay = RETRY_INITIAL_DELAY
        upload_started_at = time.perf_counter()
        while self._running:
            try:
                asset_path = await self._minio.upload_bytes(
                    response.data, template_name, data_type,
                    f"{record_id}/{filename}", content_type,
                )
                logger.debug(
                    "DownloadWorker timing: phase=minio record=%s attempts=%d "
                    "seconds=%.3f file=%s",
                    record_id,
                    retry_count + 1,
                    time.perf_counter() - upload_started_at,
                    filename,
                )
                logger.debug("DownloadWorker: uploaded %s -> %s", filename, asset_path)
                return AssetResult(
                    kind="file",
                    source_url=url,
                    final_url=response.final_url,
                    content_type=content_type,
                    asset_path=asset_path,
                )
            except Exception as exc:
                retry_count += 1
                logger.warning(
                    "DownloadWorker: MinIO upload retry %d for %s | error=%s",
                    retry_count,
                    filename,
                    type(exc).__name__,
                    exc_info=True,
                )
                if retry_count % RETRY_CRITICAL_THRESHOLD == 0:
                    logger.error(
                        "DownloadWorker: CRITICAL alert - %d MinIO upload retries "
                        "for %s",
                        retry_count,
                        filename,
                    )
                elif retry_count % RETRY_ALERT_THRESHOLD == 0:
                    logger.warning(
                        "DownloadWorker: alert - %d MinIO upload retries for %s",
                        retry_count,
                        filename,
                    )
                await asyncio.sleep(delay)
                delay = min(delay * 2, RETRY_MAX_DELAY)

        logger.info(
            "DownloadWorker: worker stopping, aborting MinIO upload for %s "
            "(attempted %d retries)",
            filename, retry_count,
        )
        return AssetResult(kind="failed", source_url=url)

    async def _download_pending_asset(
        self,
        dl_info: dict[str, Any],
        template_name: str,
        data_type: str,
        record_id: str,
        use_proxy: bool,
    ) -> tuple[dict[str, Any], AssetResult]:
        """Submit one asset to the fixed download worker pool."""
        assert self._asset_queue is not None
        result = asyncio.get_running_loop().create_future()
        await self._asset_queue.put(AssetDownloadJob(
            dl_info=dl_info,
            template_name=template_name,
            data_type=data_type,
            record_id=record_id,
            use_proxy=use_proxy,
            result=result,
        ))
        return await result

    def _start_asset_workers(self) -> None:
        self._asset_queue = asyncio.Queue()
        self._asset_workers = [
            asyncio.create_task(
                self._asset_worker(),
                name=f"download-asset-{index}",
            )
            for index in range(settings.download_asset_concurrency)
        ]

    async def _asset_worker(self) -> None:
        assert self._asset_queue is not None
        while True:
            job = await self._asset_queue.get()
            try:
                asset_result = await self._download_asset_to_minio(
                    job.dl_info,
                    job.template_name,
                    job.data_type,
                    job.record_id,
                    job.use_proxy,
                )
                if not job.result.done():
                    job.result.set_result((job.dl_info, asset_result))
            except asyncio.CancelledError:
                if not job.result.done():
                    job.result.cancel()
                raise
            except Exception as exc:
                if not job.result.done():
                    job.result.set_exception(exc)
            finally:
                self._asset_queue.task_done()

    async def _stop_asset_workers(self) -> None:
        for task in self._asset_workers:
            task.cancel()
        if self._asset_workers:
            await asyncio.gather(*self._asset_workers, return_exceptions=True)
        self._asset_workers.clear()

        if self._asset_queue is not None:
            while not self._asset_queue.empty():
                job = self._asset_queue.get_nowait()
                if not job.result.done():
                    job.result.cancel()
                self._asset_queue.task_done()
            self._asset_queue = None

    async def stop(self) -> None:
        self._running = False
        await self._stop_asset_workers()
        if self._http:
            await self._http.close()
        if self._minio:
            await self._minio.close()
        await self._mongo.close()
        logger.info("DownloadWorker stopped")
