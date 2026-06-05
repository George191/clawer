"""下载 Worker — 独立监听 MongoDB，下载资源文件并上传至 MinIO。

工作流程
--------
1. 轮询 MongoDB 中 `download_status=pending` 的记录
2. 下载专利 PDF、缩略图、附图等资源文件
3. 使用流式上传（download_bytes + upload_bytes）直接存入 MinIO，无需落盘
4. 更新 MongoDB 记录的文件路径和下载状态

设计原则
--------
- 采集与下载完全解耦：本 Worker 独立于 SpiderEngine 运行
- 流式上传：直接内存传输，节省 IO 和磁盘空间
- 幂等性：通过 MongoDB 状态字段保证重复处理安全
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.base.http import HttpClient
from app.base.minio import MinioClient
from app.base.mongo import MongoClient
from app.config.settings import settings

logger = logging.getLogger(__name__)

ASSET_IMAGE_BASE = "https://patentimages.storage.googleapis.com/"


class DownloadWorker:
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
        meta = record.get("_meta", {})
        record_id = meta.get("record_id", "")
        template_name = meta.get("template", "")

        if not record_id or not template_name:
            logger.warning("DownloadWorker: skip record with missing meta")
            return False

        async with self._semaphore:
            try:
                patent = record.get("patent", {})
                if isinstance(patent, dict) and patent:
                    return await self._download_patent_assets(
                        patent, meta, record_id
                    )

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

    async def _download_patent_assets(
        self,
        patent: dict[str, Any],
        meta: dict[str, Any],
        record_id: str,
    ) -> bool:
        template_name = meta["template"]
        data_type = meta["data_type"]
        updates: dict[str, Any] = {}

        pdf_rel = patent.get("pdf", "")
        if pdf_rel:
            pdf_path = await self._download_asset_to_minio(
                ASSET_IMAGE_BASE + pdf_rel, template_name, data_type,
                record_id, f"{record_id}.pdf",
            )
            if pdf_path:
                updates["assets.pdf"] = pdf_path

        thumb_rel = patent.get("thumbnail", "")
        if thumb_rel:
            ext = thumb_rel.rsplit(".", 1)[-1] if "." in thumb_rel else "png"
            thumb_path = await self._download_asset_to_minio(
                ASSET_IMAGE_BASE + thumb_rel, template_name, data_type,
                record_id, f"thumbnail.{ext}",
            )
            if thumb_path:
                updates["assets.thumbnail"] = thumb_path

        figures = patent.get("figures", [])
        if isinstance(figures, list):
            for i, fig in enumerate(figures):
                if not isinstance(fig, dict):
                    continue
                for key in ("thumbnail", "full"):
                    fig_rel = fig.get(key, "")
                    if not fig_rel:
                        continue
                    ext = fig_rel.rsplit(".", 1)[-1] if "." in fig_rel else "png"
                    fig_path = await self._download_asset_to_minio(
                        ASSET_IMAGE_BASE + fig_rel, template_name, data_type,
                        record_id, f"figures/{i:05d}_{key}.{ext}",
                    )
                    if fig_path:
                        updates[f"assets.figures.{i}.{key}"] = fig_path

        if updates:
            await self._mongo.update_record_fields(
                template_name, record_id, updates,
            )
            await self._mongo.update_file_status(
                template_name, record_id, "downloaded",
            )
            logger.info("DownloadWorker: downloaded %d assets for %s", len(updates), record_id)
        else:
            await self._mongo.update_file_status(
                template_name, record_id, "no_assets",
            )
        return True

    async def _download_asset_to_minio(
        self,
        url: str,
        template_name: str,
        data_type: str,
        record_id: str,
        filename: str,
    ) -> str | None:
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