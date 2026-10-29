"""Download template resources using registered datatype adapters."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
from datetime import datetime, timedelta, timezone
from time import perf_counter, time
from typing import Any
from urllib.parse import unquote

from app.base.http import DownloadError, DownloadResponse
from app.config.settings import settings
from app.downloader.adapters import (
    DownloadItem,
    get_download_adapter,
    get_download_response_validator,
    resolve_download_record,
)
from app.downloader.http_client import HttpClient
from app.engine.template_loader import TemplateLoader
from app.logger import get_logger
from app.models.template import SiteTemplate
from app.records.asset_store import AssetStore
from app.records.collection_routes import collection_route_store
from app.records.delivery import RecordDelivery
from app.records.mongo_store import MongoRecordStore
from app.utils.download_paths import iter_download_nodes, iter_download_values
from app.utils.path import get_nested_value
from app.web.services.ai_collect_store import ai_collect_store

logger = get_logger(__name__)

NOT_FOUND_ASSET = {
    "url": "",
    "description": "Source file returned HTTP 404; the file no longer exists.",
    "status_code": 404,
}


class DownloadWorker:
    """Read pending records, download their resources, and update MongoDB."""

    def __init__(
        self,
        poll_interval: int = 60,
        batch_size: int = 50,
        template_name: str | None = None,
        data_type: str | None = None,
    ) -> None:
        if poll_interval < 0 or batch_size < 1:
            raise ValueError("poll_interval must be non-negative and batch_size must be positive")
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._template_name = template_name
        self._data_type = data_type
        self._http = HttpClient()
        self._minio = AssetStore()
        self._mongo = MongoRecordStore()
        self._templates: dict[str, SiteTemplate] = {}
        self._collection_routes: dict[str, dict[str, str]] = {}
        self._running = False
        self._collection_cursor = 0
        self._delivery = RecordDelivery()
        self._downloads: set[asyncio.Task] = set()
        self._claim_heartbeats: dict[asyncio.Task, asyncio.Task] = {}
        self._collections: tuple[str, ...] | None = None
        self._routes_refreshed_at = 0.0
        self._next_claim_recovery = 0.0

    async def run(self) -> None:
        self._running = True
        logger.info(
            "DownloadWorker started: poll=%ss batch=%s template=%s datatype=%s",
            self._poll_interval, self._batch_size, self._template_name or "ALL",
            self._data_type or "ALL",
        )
        capacity = min(self._batch_size, settings.download_asset_concurrency)
        next_scan = 0.0
        prefer_delivery = False
        loop = asyncio.get_running_loop()
        try:
            while self._running:
                finished = {task for task in self._downloads if task.done()}
                self._downloads.difference_update(finished)
                for task in finished:
                    if not task.cancelled():
                        try:
                            task.result()
                        except Exception:
                            logger.exception("DownloadWorker: download task failed")
                try:
                    await self._target_collections()
                    available = capacity - len(self._downloads)
                    if available:
                        reserve_recovery = loop.time() >= next_scan and not prefer_delivery
                        queue_limit = max(available - 1, 0) if reserve_recovery else available
                        prefer_delivery = False
                        records = await self._delivery.receive(
                            collections=list(self._collection_routes), limit=queue_limit,
                        ) if queue_limit else []
                        for record in records:
                            claimed = await self._mongo.claim_download(record)
                            if claimed is not None:
                                self._downloads.add(asyncio.create_task(self._run_download(claimed)))
                        available = capacity - len(self._downloads)
                        if available and loop.time() >= next_scan:
                            before = datetime.now(timezone.utc) - timedelta(seconds=self._poll_interval)
                            records = await self._read_pending_records(limit=available, before=before)
                            for record in records:
                                self._downloads.add(asyncio.create_task(self._run_download(record)))
                            if len(records) < available:
                                next_scan = loop.time() + max(self._poll_interval, 0.5)
                            else:
                                prefer_delivery = True
                except Exception:
                    logger.exception("DownloadWorker: delivery or recovery failed")
                    next_scan = loop.time() + max(self._poll_interval, 0.5)
                if self._downloads:
                    await asyncio.wait(self._downloads, timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
                else:
                    await asyncio.sleep(0.5)
        finally:
            await self._cancel_downloads()

    async def _read_pending_records(
        self, *, limit: int | None = None, before: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Read and claim the next pending Mongo records."""
        collections = await self._target_collections()
        now = asyncio.get_running_loop().time()
        if now >= self._next_claim_recovery:
            stale_before = datetime.now(timezone.utc) - timedelta(minutes=5)
            for collection in collections:
                await self._mongo.recover_stale_downloads(collection, stale_before)
            self._next_claim_recovery = now + max(self._poll_interval, 1)
        if not collections:
            return []
        start = self._collection_cursor % len(collections)
        collections = collections[start:] + collections[:start]
        self._collection_cursor += 1
        records = []
        batch_limit = self._batch_size if limit is None else limit
        for index, collection in enumerate(collections):
            remaining = batch_limit - len(records)
            if remaining <= 0:
                break
            count = len(collections) - index
            limit = (remaining + count - 1) // count
            records.extend(await self._mongo.get_pending_downloads(
                template_name=collection, limit=limit, before=before,
            ))
        return records

    async def _target_collections(self) -> tuple[str, ...]:
        """Cache configured routes between low-frequency recovery scans."""
        now = asyncio.get_running_loop().time()
        if self._collections is not None and now - self._routes_refreshed_at < max(self._poll_interval, 1):
            return self._collections
        routes = await collection_route_store.list_by_data_type(
            self._data_type, self._template_name,
        )
        self._collection_routes = {route["collection_name"]: route for route in routes}
        self._routes_refreshed_at = now
        self._collections = tuple(self._collection_routes)
        return self._collections

    async def _run_download(self, record: dict[str, Any]) -> None:
        meta = record.get("_meta") or {}
        collection = meta.get("template")
        record_id = meta.get("record_id")
        token = meta.get("download_claim_token")
        owner = asyncio.current_task()
        heartbeat = asyncio.create_task(self._renew_claim(collection, record_id, token, owner))
        self._claim_heartbeats[owner] = heartbeat
        try:
            await self._download_record(record)
        except asyncio.CancelledError:
            try:
                await self._mongo.release_download_claim(collection, record_id, token)
            except Exception:
                logger.exception("DownloadWorker: could not release cancelled claim for %s", record_id)
            raise
        except Exception:
            logger.exception("DownloadWorker: record processing failed for %s", record_id)
        finally:
            self._claim_heartbeats.pop(owner, None)
            heartbeat.cancel()
            try:
                await asyncio.gather(heartbeat, return_exceptions=True)
            finally:
                try:
                    await self._http.release_current_task_proxy()
                except Exception:
                    logger.exception("DownloadWorker: could not release download HTTP resources for %s", record_id)

    async def _renew_claim(self, collection: str, record_id: str, token: str, owner: asyncio.Task) -> None:
        while True:
            await asyncio.sleep(60)
            try:
                if not await self._mongo.renew_download_claim(collection, record_id, token):
                    logger.warning("DownloadWorker: claim lost for %s", record_id)
                    owner.cancel()
                    return
            except Exception:
                logger.exception("DownloadWorker: could not renew claim for %s", record_id)

    async def _cancel_downloads(self) -> None:
        downloads, self._downloads = self._downloads, set()
        for task in downloads:
            task.cancel()
        await asyncio.gather(*downloads, return_exceptions=True)

    async def _download_record(self, record: dict[str, Any]) -> None:
        meta = record.get("_meta") or {}
        collection = meta.get("template")
        route = self._collection_routes.get(collection)
        if route is not None:
            template_name, data_type = route["template_name"], route["data_type"]
        else:
            template_name, data_type = resolve_download_record(record)
        record_id = meta.get("record_id")
        claim_token = meta.get("download_claim_token")
        if not collection or not record_id:
            return

        owner = meta.get("search_params") or {}
        task_id = str(owner.get("__workspace_task_id") or "")
        run_id = str(owner.get("__workspace_run_id") or "")
        started_at = perf_counter()
        message: str | None = None
        message_args: tuple[Any, ...] = ()
        level = logging.INFO
        created_at = None
        try:
            template = await self._load_template(template_name)
            if template is None:
                await self._update_mongo(collection, record_id, {}, "no_assets", claim_token)
                return

            items = self._download_template_fields(record, template.download)
            adapter = get_download_adapter(
                record, template_name=template_name, data_type=data_type,
            )
            if adapter is not None:
                items.extend(adapter(record, template, self._filename))

            if not items:
                await self._update_mongo(collection, record_id, {}, "no_assets", claim_token)
                return

            updates: dict[str, Any] = {}
            downloaded_count = 0
            reused_count = 0
            not_found_count = 0
            failed = 0
            downloaded_urls = {
                item.url: path for item in items
                if (path := self._existing_path(record, item.asset_key))
            }
            for item in items:
                existing = downloaded_urls.get(item.url)
                if existing:
                    updates[item.asset_key] = existing
                    reused_count += 1
                    continue
                try:
                    path = await self._download_item(
                        item, template_name, data_type, record_id,
                        template.effective_download_use_proxy,
                    )
                except Exception as exc:
                    logger.error(
                        "Download failed: %s | error=%s: %s",
                        item.url,
                        type(exc).__name__,
                        exc,
                    )
                    failed += 1
                    continue
                updates[item.asset_key] = path if path else dict(NOT_FOUND_ASSET)
                if path:
                    downloaded_urls[item.url] = path
                    downloaded_count += 1
                else:
                    not_found_count += 1

            completed = await self._update_mongo(
                collection, record_id, updates,
                "failed" if failed else "downloaded",
                claim_token,
            )
            if completed:
                message = (
                    "DownloadWorker: record completed | collection=%s template=%s datatype=%s record=%s status=%s "
                    "| downloaded=%d reused=%d not_found=%d failed=%d elapsed=%.2fs"
                )
                message_args = (
                    collection, template_name, data_type, record_id,
                    "failed" if failed else "downloaded",
                    downloaded_count, reused_count, not_found_count, failed,
                    perf_counter() - started_at,
                )
            else:
                message = "DownloadWorker: result not saved; claim no longer matches | collection=%s record=%s"
                message_args = (collection, record_id)
                level = logging.WARNING
            created_at = time()
            logger.log(level, message, *message_args)
            if completed and not failed and task_id and run_id:
                try:
                    await ai_collect_store.increment_run_stats(task_id, run_id, downloaded=1)
                except Exception:
                    logger.exception(
                        "Download completed but batch counter update failed: task=%s run=%s record=%s",
                        task_id, run_id, record_id,
                    )
        except Exception as exc:
            message = "DownloadWorker failed for %s | error=%s: %s"
            message_args = (record_id, type(exc).__name__, exc)
            level = logging.ERROR
            created_at = time()
            logger.log(level, message, *message_args)
            heartbeat = self._claim_heartbeats.get(asyncio.current_task())
            if heartbeat is not None:
                heartbeat.cancel()
            await self._mongo.update_file_status(
                collection, record_id, "failed", claim_token=claim_token,
            )
        finally:
            if message is not None and task_id and run_id:
                try:
                    await ai_collect_store.write_task_log(
                        task_id, run_id, "Downloader", level, message, *message_args,
                        created_at=created_at,
                    )
                except Exception:
                    logger.exception(
                        "Download task log write failed: task=%s run=%s record=%s",
                        task_id, run_id, record_id,
                    )

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
            collection = getattr(config, "collection", None)
            meta = record.get("_meta") or {}
            if collection and collection != meta.get("template"):
                continue
            recursive = getattr(config, "recursive", None)
            if collection or recursive or "[]" in config.selector or "*" in config.selector:
                for prefix, node in iter_download_nodes(record, recursive):
                    for path, value in iter_download_values(node, config.selector):
                        full_path = f"{prefix}.{path}" if prefix else path
                        for field, url in self._urls_from_value(value, config):
                            if not url:
                                continue
                            key = f"assets.{full_path}"
                            if isinstance(value, dict):
                                key += f".{field}"
                            suffix = "_" + hashlib.md5(key.encode()).hexdigest()[:10]
                            items.append(DownloadItem(url=url, filename=self._filename(url, suffix), asset_key=key))
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
        started_at = perf_counter()
        response: DownloadResponse | None = None
        for attempt in range(5):
            try:
                response = await self._http.download_response(item.url, use_proxy=use_proxy)
                break
            except Exception as exc:
                status_code = HttpClient._error_status_code(exc)
                if status_code == 404:
                    return None
                if status_code and status_code >= 400 and status_code not in settings.http_retry_on_statuses:
                    raise
                logger.warning(
                    "DownloadWorker: retry %d/5 for %s | error=%s: %s | status=%s",
                    attempt + 1,
                    item.url,
                    type(exc).__name__,
                    exc,
                    status_code or 0,
                )
                if attempt == 4:
                    raise
                await asyncio.sleep(min(2 ** attempt, 8))
        if response is None:
            return None
        content_type = response.content_type or "application/octet-stream"
        response_data = response.data
        validator = get_download_response_validator(template_name)
        if validator is not None:
            validator(item, response)
        if item.attachment and content_type.startswith("text/html"):
            raise DownloadError(item.url, response.status_code, "attachment is HTML")
        path = await self._minio.upload_bytes(
            response_data,
            template_name,
            data_type,
            f"{record_id}/{item.filename}",
            content_type,
        )
        logger.info(
            "Download uploaded: %s | template=%s datatype=%s record=%s "
            "| bytes=%d elapsed=%.2fs | stored=%s",
            item.url, template_name, data_type, record_id,
            len(response_data), perf_counter() - started_at, path,
        )
        return path

    async def _update_mongo(
        self,
        collection: str,
        record_id: str,
        updates: dict[str, Any],
        status: str,
        claim_token: str,
    ) -> bool:
        # Persistence ends the claim; its Kafka ACK can take longer than a heartbeat.
        heartbeat = self._claim_heartbeats.get(asyncio.current_task())
        if heartbeat is not None:
            heartbeat.cancel()
        return await self._mongo.update_download_result(
            collection, record_id, updates, status, claim_token=claim_token,
        )

    async def _load_template(self, name: str) -> SiteTemplate | None:
        if name in self._templates:
            return self._templates[name]
        try:
            released = await TemplateLoader().load_released(
                name, validate_params=False, load_adapter=False,
            )
        except Exception as exc:
            logger.error(
                "Cannot load template: %s | error=%s: %s",
                name,
                type(exc).__name__,
                exc,
            )
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
        await self._cancel_downloads()
        await self._delivery.close()
        await self._http.close()
        await self._minio.close()
        await self._mongo.close()
        logger.info("DownloadWorker stopped")
