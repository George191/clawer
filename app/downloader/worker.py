"""下载 Worker — 独立监听 MongoDB，下载资源文件并上传至 MinIO。

工作流程
--------
1. 轮询 MongoDB 中 `download_status=pending` 的记录
2. 根据模板的 download 配置提取下载链接
3. 使用流式上传（download_bytes + upload_bytes）直接存入 MinIO，无需落盘
4. 更新 MongoDB 记录的文件路径和下载状态

设计原则
--------
- 采集与下载完全解耦：本 Worker 独立于 SpiderEngine 运行
- 流式上传：直接内存传输，节省 IO 和磁盘空间
- 幂等性：通过 MongoDB 状态字段保证重复处理安全
- 模板驱动：根据 YAML 模板中的 download 配置自动选择下载策略
  支持 JSON 路径提取（如 Google Patents）和 CSS 选择器提取（如 Sealagom PDF）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.base.http import HttpClient
from app.base.minio import MinioClient
from app.base.mongo import MongoClient
from app.config.settings import settings
from app.engine.template_loader import TemplateLoader
from app.models.template import SiteTemplate

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._http: HttpClient | None = None
        self._minio: MinioClient | None = None
        self._mongo = MongoClient()
        self._semaphore: asyncio.Semaphore | None = None
        self._running = False
        self._template_loader = TemplateLoader()
        # 模板缓存：避免每次下载都重新加载 YAML
        self._template_cache: dict[str, SiteTemplate] = {}
        # 无下载需求模板缓存：避免重复加载无 download 配置的模板
        self._no_assets_templates: set[str] = set()

    async def run(self) -> None:
        self._running = True
        self._http = HttpClient()
        self._minio = MinioClient()
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)

        logger.info(
            "DownloadWorker started (poll=%ds, batch=%d, concurrency=%d)",
            self._poll_interval,
            self._batch_size,
            settings.max_concurrent_tasks,
        )

        # 启动诊断：输出所有集合的下载状态概览
        await self._log_startup_stats()

        while self._running:
            try:
                count = await self._process_batch()
                if count == 0:
                    await asyncio.sleep(self._poll_interval)
            except Exception:
                logger.exception("DownloadWorker loop error")
                await asyncio.sleep(self._poll_interval)

    async def _process_batch(self) -> int:
        pending = await self._mongo.get_pending_downloads(limit=self._batch_size)
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
        template_name = meta.get("template", "")

        if not record_id or not template_name:
            logger.warning("DownloadWorker: skip record with missing meta")
            return False

        async with self._semaphore:
            try:
                # 快速路径：模板已知无下载需求，直接标记
                if template_name in self._no_assets_templates:
                    await self._mongo.update_file_status(
                        template_name, record_id, "no_assets",
                    )
                    return True

                # 加载模板
                template = await self._get_template(template_name)
                if template is None:
                    await self._mongo.update_file_status(
                        template_name, record_id, "no_assets",
                    )
                    return True

                download_config = template.download
                if download_config is None:
                    # 缓存该模板，后续扫描跳过
                    self._no_assets_templates.add(template_name)
                    logger.info(
                        "DownloadWorker: template '%s' has no download config, "
                        "caching as no_assets",
                        template_name,
                    )
                    await self._mongo.update_file_status(
                        template_name, record_id, "no_assets",
                    )
                    return True

                # 提取下载 URL
                download_urls = self._extract_download_urls(
                    record, download_config, template_name,
                )

                if not download_urls:
                    await self._mongo.update_file_status(
                        template_name, record_id, "no_assets",
                    )
                    return True

                # 下载并上传到 MinIO
                data_type = meta["data_type"]
                updates: dict[str, Any] = {}

                for idx, dl_info in enumerate(download_urls):
                    url = dl_info["url"]
                    filename = dl_info["filename"]

                    asset_path = await self._download_asset_to_minio(
                        url, template_name, data_type,
                        record_id, filename,
                    )
                    if asset_path:
                        key = dl_info.get("asset_key", f"assets.{idx}")
                        updates[key] = asset_path

                if updates:
                    await self._mongo.update_record_fields(
                        template_name, record_id, updates,
                    )
                    await self._mongo.update_file_status(
                        template_name, record_id, "downloaded",
                    )
                    logger.info(
                        "DownloadWorker: downloaded %d assets for %s",
                        len(updates), record_id,
                    )
                else:
                    await self._mongo.update_file_status(
                        template_name, record_id, "no_assets",
                    )
                return True

            except Exception:
                logger.exception("DownloadWorker: failed for %s", record_id)
                try:
                    await self._mongo.update_file_status(
                        template_name, record_id, "", "failed",
                    )
                except Exception:
                    pass
                return False

    async def _get_template(self, template_name: str) -> SiteTemplate | None:
        """获取模板（带缓存）。"""
        if template_name in self._template_cache:
            return self._template_cache[template_name]

        try:
            template = self._template_loader.load(template_name)
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
            stats = await self._mongo.get_collection_stats()
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
        except Exception:
            logger.warning("DownloadWorker: failed to get collection stats")

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
        elif selector_type == "css":
            urls = self._extract_css_urls(record, download_config)
        else:
            logger.warning(
                "DownloadWorker: unsupported selector_type '%s' for '%s'",
                selector_type, template_name,
            )

        return urls

    def _extract_json_urls(
        self,
        record: dict[str, Any],
        download_config: Any,
    ) -> list[dict[str, Any]]:
        """从 JSON 记录中提取下载 URL。

        支持嵌套路径，如 'patent.pdf' → record['patent']['pdf']
        支持列表展开，如 'patent.figures' → 遍历列表中的每项
        """
        from app.parser.template_parser import resolve_json_path

        selector = download_config.selector
        url_prefix = getattr(download_config, 'url_prefix', None) or ""
        file_ext = getattr(download_config, 'file_extension', None)

        # 提取原始值
        raw_value = resolve_json_path(record, selector)

        if raw_value is None:
            logger.debug("DownloadWorker: no value at path '%s'", selector)
            return []

        # 如果是列表，展开处理
        if isinstance(raw_value, list):
            urls = []
            for i, item in enumerate(raw_value):
                if isinstance(item, dict):
                    # 复合对象：尝试提取 href/src 等字段
                    sub_url = self._extract_url_from_dict(
                        item, download_config.link_type, url_prefix,
                    )
                    if sub_url:
                        filename = self._make_filename(
                            sub_url, file_ext, suffix=f"_{i:05d}",
                        )
                        urls.append({
                            "url": sub_url,
                            "filename": filename,
                            "asset_key": f"{selector}.{i}",
                        })
                elif isinstance(item, str):
                    full_url = url_prefix + item
                    filename = self._make_filename(
                        full_url, file_ext, suffix=f"_{i:05d}",
                    )
                    urls.append({
                        "url": full_url,
                        "filename": filename,
                        "asset_key": f"{selector}.{i}",
                    })
            return urls

        # 单值处理
        if isinstance(raw_value, dict):
            # 复合对象：尝试提取 href/src 等字段
            sub_url = self._extract_url_from_dict(
                raw_value, download_config.link_type, url_prefix,
            )
            if sub_url:
                return [{
                    "url": sub_url,
                    "filename": self._make_filename(sub_url, file_ext),
                    "asset_key": selector,
                }]
            return []

        # 字符串值
        full_url = url_prefix + str(raw_value)
        return [{
            "url": full_url,
            "filename": self._make_filename(full_url, file_ext),
            "asset_key": selector,
        }]

    def _extract_css_urls(
        self,
        record: dict[str, Any],
        download_config: Any,
    ) -> list[dict[str, Any]]:
        """CSS 选择器模式（未来扩展：重新请求页面解析）。"""
        logger.warning("DownloadWorker: CSS selector extraction not yet implemented")
        return []

    def _extract_url_from_dict(
        self,
        data: dict[str, Any],
        link_type: str,
        url_prefix: str,
    ) -> str | None:
        """从字典中提取 URL。"""
        # 尝试常见字段名
        for key in ("href", "src", "url", "link", "thumbnail", "full", "pdf"):
            if key in data and data[key]:
                val = str(data[key])
                return url_prefix + val if not val.startswith("http") else val
        return None

    @staticmethod
    def _make_filename(
        url: str,
        file_ext: str | None = None,
        suffix: str = "",
    ) -> str:
        """从 URL 或扩展名生成文件名。"""
        if file_ext:
            ext = file_ext.lstrip(".")
        else:
            # 从 URL 中提取扩展名
            path_part = url.split("?")[0]
            if "." in path_part.rsplit("/", 1)[-1]:
                ext = path_part.rsplit(".", 1)[-1].lower()
            else:
                ext = "bin"

        # 安全文件名
        name_part = url.split("?")[0].rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if not name_part or len(name_part) > 60:
            # URL 最后一段不合适，用 hash
            import hashlib
            name_part = hashlib.md5(url.encode()).hexdigest()[:12]

        return f"{name_part}{suffix}.{ext}"

    async def _download_asset_to_minio(
        self,
        url: str,
        template_name: str,
        data_type: str,
        record_id: str,
        filename: str,
    ) -> str | None:
        """下载单个资源文件并上传到 MinIO。"""
        content_type = MinioClient._guess_content_type(filename)

        try:
            data = await self._http.download_bytes(url)
        except Exception:
            logger.error("DownloadWorker: failed to download asset %s", url)
            return None

        try:
            asset_path = await self._minio.upload_bytes(
                data, template_name, data_type,
                f"{record_id}/{filename}", content_type,
            )
            logger.debug("DownloadWorker: uploaded %s -> %s", filename, asset_path)
            return asset_path
        except Exception:
            logger.exception("DownloadWorker: MinIO upload failed for %s", filename)
            return None

    async def stop(self) -> None:
        self._running = False
        if self._http:
            await self._http.close()
        if self._minio:
            await self._minio.close()
        await self._mongo.close()
        logger.info("DownloadWorker stopped")