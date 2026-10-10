"""Download configured resources and extracted news assets."""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from app.base.http import DownloadError, DownloadResponse, HttpClient
from app.base.minio import MinioClient
from app.base.mongo import MongoClient
from app.config.settings import settings
from app.engine.template_loader import TemplateLoader
from app.logger import get_logger
from app.models.template import SiteTemplate
from app.utils.path import get_nested_value

logger = get_logger(__name__)

DOWNLOAD_COLLECTION_OVERRIDES = {"sec_edgar": "sec_edgar_filing"}
DOWNLOAD_TEMPLATE_OVERRIDES = {
    collection: template
    for template, collection in DOWNLOAD_COLLECTION_OVERRIDES.items()
}
NEWS_ASSET_FIELDS = {"attachments", "images", "videos", "audios"}
NOT_FOUND_ASSET = {
    "url": "",
    "description": "Source file returned HTTP 404; the file no longer exists.",
    "status_code": 404,
}


@dataclass(frozen=True, slots=True)
class DownloadItem:
    url: str
    filename: str
    asset_key: str
    attachment: bool = False


class DownloadWorker:
    """Read pending records, download their resources, and update MongoDB."""

    def __init__(
        self,
        poll_interval: int = 10,
        batch_size: int = 50,
        template_name: str | None = None,
    ) -> None:
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._template_name = template_name
        self._http = HttpClient()
        self._minio = MinioClient()
        self._mongo = MongoClient()
        self._templates: dict[str, SiteTemplate] = {}
        self._running = False

    @property
    def _query_template_name(self) -> str | None:
        if self._template_name is None:
            return None
        return DOWNLOAD_COLLECTION_OVERRIDES.get(
            self._template_name, self._template_name,
        )

    async def run(self) -> None:
        self._running = True
        logger.info(
            "DownloadWorker started: poll=%ss batch=%s template=%s",
            self._poll_interval, self._batch_size, self._template_name or "ALL",
        )
        await self._log_pending_summary()
        while self._running:
            records = await self._read_pending_records()
            if records:
                logger.info(
                    "DownloadWorker: claimed %d records for template=%s",
                    len(records), self._template_name or "ALL",
                )
                await self._download_list(records)
            else:
                await self._log_pending_summary()
                await asyncio.sleep(self._poll_interval)

    async def _read_pending_records(self) -> list[dict[str, Any]]:
        """Read and claim the next pending Mongo records."""
        return await self._mongo.get_pending_downloads(
            template_name=self._query_template_name,
            limit=self._batch_size,
        )

    async def _log_pending_summary(self) -> None:
        """Log pending counts for the selected template or all collections."""
        try:
            stats = await self._mongo.get_collection_stats(self._query_template_name)
        except Exception:
            logger.exception("DownloadWorker: failed to read pending summary")
            return

        pending = sum(int(item.get("pending_download") or 0) for item in stats)
        scope = self._template_name or "ALL"
        logger.info(
            "DownloadWorker: pending downloads=%d template=%s collections=%d",
            pending, scope, len(stats),
        )
        for item in stats:
            logger.info(
                "DownloadWorker: collection=%s pending=%d total=%d",
                item.get("name", ""),
                int(item.get("pending_download") or 0),
                int(item.get("total") or 0),
            )

    async def _download_list(self, records: list[dict[str, Any]]) -> None:
        """Download a claimed record list with bounded concurrency."""
        semaphore = asyncio.Semaphore(settings.max_concurrent_tasks)

        async def download(record: dict[str, Any]) -> None:
            async with semaphore:
                await self._download_record(record)

        await asyncio.gather(*(download(record) for record in records))

    async def _download_record(self, record: dict[str, Any]) -> None:
        meta = record.get("_meta")
        collection = meta.get("template")
        template_name = DOWNLOAD_TEMPLATE_OVERRIDES.get(collection, collection)
        record_id = meta.get("record_id")
        claim_token = meta.get("download_claim_token")
        if not collection or not record_id:
            return

        try:
            template = await self._load_template(template_name)
            if template is None:
                await self._update_mongo(collection, record_id, {}, "no_assets", claim_token)
                return

            data_type = meta.get("data_type").lower()
            items = self._download_template_fields(record, template.download)

            if data_type == "news":
                items.extend(self._download_news_fields(record))

            if not items:
                await self._update_mongo(collection, record_id, {}, "no_assets", claim_token)
                return

            updates: dict[str, Any] = {}
            failed = False
            for item in items:
                existing = self._existing_path(record, item.asset_key)
                if existing:
                    updates[item.asset_key] = existing
                    continue
                try:
                    path = await self._download_item(
                        item, template_name, data_type, record_id,
                        template.effective_download_use_proxy,
                    )
                except Exception:
                    logger.exception("Download failed: %s", item.url)
                    failed = True
                    continue
                updates[item.asset_key] = path if path else dict(NOT_FOUND_ASSET)

            await self._update_mongo(
                collection, record_id, updates,
                "failed" if failed else "downloaded",
                claim_token,
            )
        except Exception:
            logger.exception("DownloadWorker failed for %s", record_id)
            await self._mongo.update_file_status(
                collection, record_id, "failed", claim_token=claim_token,
            )

    def _download_news_fields(
        self,
        record: dict[str, Any],
    ) -> list[DownloadItem]:
        """Download asset fields extracted by the news asset helper."""
        items: list[DownloadItem] = []
        for field in NEWS_ASSET_FIELDS:
            values = record.get(field)
            if not isinstance(values, list):
                continue
            for index, value in enumerate(values):
                url = value.get("url") if isinstance(value, dict) else value
                if not url:
                    continue
                items.append(DownloadItem(
                    url=str(url),
                    filename=self._filename(str(url), f"_{index:05d}"),
                    asset_key=(
                        f"assets.{field}.{index}.url"
                        if isinstance(value, dict) else f"assets.{field}.{index}"
                    ),
                    attachment=field == "attachments",
                ))

        return items

    def _download_template_fields(
        self,
        record: dict[str, Any],
        configs: list[Any],
    ) -> list[DownloadItem]:
        items: list[DownloadItem] = []
        for config in configs:
            selector_type = getattr(config.selector_type, "value", config.selector_type)
            if selector_type != "json":
                continue
            value = get_nested_value(record, config.selector)
            values = value if isinstance(value, list) else [value]
            for index, entry in enumerate(values):
                for field, url in self._urls_from_value(entry, config):
                    suffix = f"_{index:05d}" if len(values) > 1 else ""
                    key = f"assets.{config.selector}"
                    if len(values) > 1:
                        key += f".{index}"
                    if isinstance(entry, dict):
                        key += f".{field}"
                    items.append(DownloadItem(
                        url=url,
                        filename=self._filename(url, suffix),
                        asset_key=key,
                    ))
        return items

    @staticmethod
    def _urls_from_value(value: Any, config: Any) -> list[tuple[str, str]]:
        if value is None:
            return []
        prefix = getattr(config, "url_prefix", None) or ""
        if isinstance(value, dict):
            fields = ("href", "src", "url", "link", "full", "thumbnail", "pdf")
            return [
                (field, prefix + str(value[field]))
                for field in fields if value.get(field)
            ]
        if isinstance(value, str):
            return [("url", prefix + value)]
        return []

    async def _download_item(
        self,
        item: DownloadItem,
        template_name: str,
        data_type: str,
        record_id: str,
        use_proxy: bool,
    ) -> str | None:
        response: DownloadResponse | None = None
        for attempt in range(5):
            try:
                response = await self._http.download_response(item.url, use_proxy=use_proxy)
                break
            except DownloadError as exc:
                if exc.status_code == 404:
                    return None
                if attempt == 4:
                    raise
                await asyncio.sleep(min(2 ** attempt, 8))
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                logger.warning(
                    "DownloadWorker: retry %d/5 for %s | error=%s | status=%s",
                    attempt + 1,
                    item.url,
                    type(exc).__name__,
                    status_code or 0,
                )
                await self._http.release_current_task_proxy()
                if attempt == 4:
                    raise
                await asyncio.sleep(min(2 ** attempt, 8))
        if response is None:
            return None
        content_type = response.content_type or "application/octet-stream"
        if item.attachment and content_type.startswith("text/html"):
            raise DownloadError(item.url, response.status_code, "attachment is HTML")
        return await self._minio.upload_bytes(
            response.data,
            template_name,
            data_type,
            f"{record_id}/{item.filename}",
            content_type,
        )

    async def _update_mongo(
        self,
        collection: str,
        record_id: str,
        updates: dict[str, Any],
        status: str,
        claim_token: str,
    ) -> None:
        updates = dict(updates)
        updates["_meta.sync_status"] = "pending"
        await self._mongo.update_download_result(
            collection, record_id, updates, status, claim_token=claim_token,
        )

    async def _load_template(self, name: str) -> SiteTemplate | None:
        if name in self._templates:
            return self._templates[name]
        try:
            released = await TemplateLoader().load_released(
                name, validate_params=False, load_adapter=False,
            )
        except Exception:
            logger.exception("Cannot load template: %s", name)
            return None
        self._templates[name] = released.template
        return released.template

    @staticmethod
    def _existing_path(record: dict[str, Any], key: str) -> str:
        value = get_nested_value(record, key)
        if isinstance(value, dict):
            value = value.get("url")
        return str(value).strip() if isinstance(value, str) else ""

    @staticmethod
    def _filename(url: str, suffix: str = "") -> str:
        name = unquote(url.split("?", 1)[0].rsplit("/", 1)[-1])
        stem, dot, ext = name.rpartition(".")
        if not stem or len(stem) > 60:
            stem = hashlib.md5(url.encode()).hexdigest()[:12]
        if not dot or not ext:
            ext = mimetypes.guess_extension("application/octet-stream")
            ext = ext.lstrip(".")
        return f"{stem}{suffix}.{ext}"

    async def stop(self) -> None:
        self._running = False
        await self._http.close()
        await self._minio.close()
        await self._mongo.close()
        logger.info("DownloadWorker stopped")
