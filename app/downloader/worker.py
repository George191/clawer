"""Download template resources using registered datatype adapters."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from time import perf_counter
from typing import Any

import filetype

from app.base.http import DownloadCleanupError, DownloadError
from app.config.settings import settings
from app.downloader.adapters import (
    DownloadItem,
    get_download_response_validator,
    resolve_download_record,
)
from app.downloader.http_client import HttpClient
from app.engine.template_loader import TemplateLoader
from app.logger import get_logger
from app.models.template import SiteTemplate
from app.records.asset_store import AssetStore
from app.records.change_feed import MongoChangeFeed, mongo_cdc_topic_prefix
from app.records.collection_routes import collection_route_store
from app.records.download_resources import (
    decode_manifest,
    decode_plan,
    download_items,
    encode_manifest,
    encode_plan,
    item_fingerprint,
    resource_manifest,
    source_fingerprint,
)
from app.records.mongo_store import MongoRecordStore
from app.utils.path import get_nested_value
from app.utils.runtime_control import check_control_state
from app.web.services.ai_collect_store import ai_collect_store

logger = get_logger(__name__)
DOWNLOAD_PROGRESS_INTERVAL_SECONDS = 2
IDLE_COLLECTION_SCAN_INTERVAL_SECONDS = 600

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
        self._templates_checked_at: dict[str, float] = {}
        self._collection_routes: dict[str, dict[str, str]] = {}
        self._running = False
        self._collection_cursor = 0
        self._changes = MongoChangeFeed(
            role="downloader", data_type=data_type, template_name=template_name,
        )
        self._downloads: set[asyncio.Task] = set()
        self._claim_heartbeats: dict[asyncio.Task, asyncio.Task] = {}
        self._collections: tuple[str, ...] | None = None
        self._routes_refreshed_at = 0.0
        self._next_claim_recovery = 0.0
        self._idle_collections: dict[str, float] = {}

    async def run(self) -> None:
        self._running = True
        logger.info(
            "DownloadWorker started: poll=%ss batch=%s template=%s datatype=%s",
            self._poll_interval, self._batch_size, self._template_name or "ALL",
            self._data_type or "ALL",
        )
        capacity = min(self._batch_size, settings.download_asset_concurrency)
        next_scan = 0.0
        prefer_changes = False
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
                        reserve_recovery = loop.time() >= next_scan and not prefer_changes
                        queue_limit = max(available - 1, 0) if reserve_recovery else available
                        prefer_changes = False
                        try:
                            records = await self._changes.receive(
                                collections=list(self._collection_routes), limit=queue_limit,
                            ) if queue_limit else []
                        except Exception:
                            logger.exception("DownloadWorker: CDC unavailable; continuing Mongo recovery")
                            records = []
                        for record in records:
                            self._idle_collections.pop((record.get("_meta") or {}).get("template"), None)
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
                                prefer_changes = True
                    else:
                        await self._changes.keep_alive()
                except Exception:
                    logger.exception("DownloadWorker: change feed or recovery failed")
                    next_scan = loop.time() + max(self._poll_interval, 0.5)
                if self._downloads:
                    await asyncio.wait(self._downloads, timeout=0.5, return_when=asyncio.FIRST_COMPLETED)
                elif not self._changes.has_more:
                    await asyncio.sleep(0.5)
        finally:
            await self._cancel_downloads()

    async def _read_pending_records(
        self, *, limit: int | None = None, before: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Read and claim the next pending Mongo records."""
        collections = await self._target_collections()
        now = asyncio.get_running_loop().time()
        collections = tuple(
            name for name in collections if now >= self._idle_collections.get(name, 0)
        )
        if not collections:
            return []
        if now >= self._next_claim_recovery:
            stale_before = datetime.now(timezone.utc) - timedelta(minutes=5)
            for collection in collections:
                await self._mongo.recover_stale_downloads(collection, stale_before)
            self._next_claim_recovery = now + max(self._poll_interval, 1)
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
            pending = await self._mongo.get_pending_downloads(
                template_name=collection, limit=limit, before=before,
            )
            records.extend(pending)
            if pending or await self._mongo.has_unfinished_downloads(collection):
                self._idle_collections.pop(collection, None)
            else:
                self._idle_collections[collection] = (
                    asyncio.get_running_loop().time() + IDLE_COLLECTION_SCAN_INTERVAL_SECONDS
                )
        return records

    async def _target_collections(self) -> tuple[str, ...]:
        """Cache configured routes between low-frequency recovery scans."""
        now = asyncio.get_running_loop().time()
        if self._collections is not None and now - self._routes_refreshed_at < max(self._poll_interval, 1):
            return self._collections
        routes = await collection_route_store.list_by_data_type(
            self._data_type, self._template_name,
        )
        paused = set(settings.kafka_paused_topics)
        routes = [
            route for route in routes
            if f"{mongo_cdc_topic_prefix()}.{settings.db_name}.{route['collection_name']}" not in paused
            and f"{settings.kafka_topic}-{route['data_type']}" not in paused
        ]
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
        items_total = 0
        downloaded_count = 0
        reused_count = 0
        not_found_count = 0
        failed = 0
        errors: list[str] = []
        processed = 0
        status = "preparing"
        last_notified = 0.0
        writing_result = False

        async def notify(*, force: bool = False) -> None:
            nonlocal last_notified
            if not task_id or not run_id:
                return
            if not force and perf_counter() - last_notified < DOWNLOAD_PROGRESS_INTERVAL_SECONDS:
                return
            try:
                await asyncio.wait_for(
                    ai_collect_store.write_task_log(
                        task_id, run_id, "Downloader", logging.INFO,
                        "download progress: record_id=%s items_completed=%d items_total=%d status=%s "
                        "items_downloaded=%d items_reused=%d items_missing=%d items_failed=%d items_processed=%d",
                        record_id, downloaded_count + reused_count, items_total, status,
                        downloaded_count, reused_count, not_found_count, failed, processed,
                        progress_prefix="download progress:",
                    ),
                    timeout=DOWNLOAD_PROGRESS_INTERVAL_SECONDS,
                )
            except Exception:
                logger.warning(
                    "Download progress notification failed: task=%s run=%s record=%s",
                    task_id, run_id, record_id, exc_info=True,
                    extra={"skip_task_log_capture": True},
                )
            finally:
                last_notified = perf_counter()

        try:
            await notify(force=True)
            template = await self._load_template(template_name)
            if template is None:
                writing_result = True
                await self._update_mongo(collection, record_id, self._retry_updates(
                    "Download template unavailable",
                ), "pending", claim_token)
                status = "pending_retry"
                return

            if "download_plan" in meta:
                items = decode_plan(meta["download_plan"])
                manifest = resource_manifest(items)
            else:
                items, manifest = self._plan_download(record, template, template_name, data_type)
            if "download_plan" not in meta and "download_inputs" in meta and manifest != decode_manifest(meta["download_inputs"]):
                template = await self._load_template(template_name, refresh=True)
                if template is not None:
                    items, manifest = self._plan_download(record, template, template_name, data_type)
                if template is None:
                    writing_result = True
                    await self._update_mongo(collection, record_id, self._retry_updates(
                        "Download template unavailable",
                    ), "pending", claim_token)
                    status = "pending_retry"
                    return
            if "download_inputs" in meta and manifest != decode_manifest(meta["download_inputs"]):
                writing_result = True
                await self._update_mongo(collection, record_id, self._retry_updates(
                    "Resource plan does not match saved inputs; recrawl required",
                ), "pending", claim_token)
                status = "pending_retry"
                logger.error("Download resource plan cannot be verified; recrawl required: %s", record_id)
                return
            items_total = len(items)
            completed_inputs = decode_manifest(meta.get("downloaded_inputs"))
            stale_assets = set(decode_manifest(meta.get("download_inputs"))) - set(manifest)
            stale_assets.update(key for key, fingerprint in manifest.items() if completed_inputs.get(key) != fingerprint)
            if not items:
                writing_result = True
                completed = await self._update_mongo(collection, record_id, {
                    "_meta.download_inputs": [], "_meta.downloaded_inputs": [], "_meta.download_plan": [],
                }, "no_assets", claim_token, unset_fields=stale_assets)
                status = "no_assets" if completed else "claim_lost"
                return

            updates: dict[str, Any] = {}
            status = "downloading"
            await notify(force=True)
            downloaded_urls = {
                source_fingerprint(item): path for item in items
                if completed_inputs.get(item.asset_key) == item_fingerprint(item)
                and (path := self._existing_path(record, item.asset_key))
            }
            verified_inputs = {}
            for item in items:
                fingerprint = item_fingerprint(item)
                source = source_fingerprint(item)
                previous = get_nested_value(record, item.asset_key)
                if (completed_inputs.get(item.asset_key) == fingerprint
                        and isinstance(previous, dict) and previous.get("status_code") == 404):
                    updates[item.asset_key] = previous
                    verified_inputs[item.asset_key] = fingerprint
                    not_found_count += 1
                    processed += 1
                    await notify()
                    continue
                existing = downloaded_urls.get(source)
                if existing:
                    updates[item.asset_key] = existing
                    verified_inputs[item.asset_key] = fingerprint
                    reused_count += 1
                    processed += 1
                    await notify()
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
                    errors.append(f"{type(exc).__name__}: {exc}")
                    processed += 1
                    await notify()
                    continue
                updates[item.asset_key] = path if path else dict(NOT_FOUND_ASSET)
                verified_inputs[item.asset_key] = fingerprint
                if path:
                    downloaded_urls[source] = path
                    downloaded_count += 1
                else:
                    not_found_count += 1
                processed += 1
                await notify()

            updates.update({
                "_meta.download_inputs": encode_manifest(manifest),
                "_meta.download_plan": encode_plan(items),
                "_meta.downloaded_inputs": encode_manifest(verified_inputs),
            })
            download_status = "pending" if failed else "downloaded"
            if failed:
                updates.update(self._retry_updates("; ".join(errors)))
            writing_result = True
            completed = await self._update_mongo(
                collection, record_id, updates,
                download_status,
                claim_token, unset_fields=stale_assets - set(updates),
            )
            if completed:
                status = "pending_retry" if failed else "complete"
                logger.log(
                    logging.WARNING if failed else logging.DEBUG,
                    "DownloadWorker: record completed | collection=%s template=%s datatype=%s record=%s status=%s "
                    "| downloaded=%d reused=%d not_found=%d failed=%d elapsed=%.2fs",
                    collection, template_name, data_type, record_id,
                    download_status,
                    downloaded_count, reused_count, not_found_count, failed,
                    perf_counter() - started_at,
                    extra={"skip_task_log_capture": not failed},
                )
            else:
                status = "claim_lost"
                logger.warning(
                    "DownloadWorker: result not saved; claim no longer matches | collection=%s record=%s",
                    collection, record_id,
                )
            if completed and not failed and task_id and run_id:
                try:
                    await ai_collect_store.increment_run_stats(task_id, run_id, downloaded=1)
                except Exception:
                    logger.exception(
                        "Download completed but batch counter update failed: task=%s run=%s record=%s",
                        task_id, run_id, record_id,
                    )
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as exc:
            # A result write may already have committed. Let the persisted state
            # or the expired claim drive recovery instead of overwriting it.
            if writing_result:
                status = "pending_retry"
                raise
            status = "pending_retry"
            logger.error("DownloadWorker failed for %s | error=%s: %s", record_id, type(exc).__name__, exc)
            error = f"{type(exc).__name__}: {exc}"
            await self._update_mongo(
                collection, record_id,
                self._retry_updates(error), "pending", claim_token,
            )
        finally:
            await notify(force=True)

    def _plan_download(
        self, record: dict[str, Any], template: SiteTemplate, template_name: str, data_type: str,
    ) -> tuple[list[DownloadItem], dict[str, str]]:
        """Generate resource inputs through the same template and adapter contract."""
        items = download_items(record, template, template_name, data_type)
        return items, resource_manifest(items)

    def _retry_updates(self, error: str) -> dict[str, Any]:
        return {
            "_meta.download_retry_at": datetime.now(timezone.utc) + timedelta(seconds=max(self._poll_interval, 0.5)),
            "_meta.download_error": error,
        }

    async def _download_item(
        self,
        item: DownloadItem,
        template_name: str,
        data_type: str,
        record_id: str,
        use_proxy: bool,
    ) -> str | None:
        started_at = perf_counter()
        try:
            # One attempt per scan; include proxy acquisition in the fetch budget.
            async with asyncio.timeout(settings.http_download_timeout):
                await check_control_state()
                try:
                    response = await self._http.download_response(item.url, use_proxy=use_proxy)
                    validator = get_download_response_validator(template_name)
                    if validator is not None:
                        validator(item, response)
                    if item.attachment and (response.content_type or "").startswith("text/html"):
                        raise DownloadError(item.url, response.status_code, "attachment is HTML")
                except DownloadCleanupError:
                    raise
                except Exception as exc:
                    if HttpClient._error_status_code(exc) == 404:
                        return None
                    await self._http.mark_last_proxy_failed()
                    raise
        except TimeoutError as exc:
            raise DownloadError(
                item.url, message=f"resource fetch exceeded its {settings.http_download_timeout}s budget",
            ) from exc
        response_data = response.data
        content_type = response.content_type or "application/octet-stream"
        filename = item.filename
        detected_type = filetype.guess(response_data)
        if detected_type is not None:
            content_type = detected_type.mime
            filename += f".{detected_type.extension}"
        path = await self._minio.upload_bytes(
            response_data,
            template_name,
            data_type,
            f"{record_id}/{source_fingerprint(item)}/{hashlib.sha256(response_data).hexdigest()}/{filename}",
            content_type,
        )
        logger.debug(
            "Download uploaded: %s | template=%s datatype=%s record=%s "
            "| bytes=%d elapsed=%.2fs | stored=%s",
            item.url, template_name, data_type, record_id,
            len(response_data), perf_counter() - started_at, path,
            extra={"skip_task_log_capture": True},
        )
        return path

    async def _update_mongo(
        self,
        collection: str,
        record_id: str,
        updates: dict[str, Any],
        status: str,
        claim_token: str,
        *, unset_fields: set[str] | None = None,
    ) -> bool:
        # The final state write ends the claim.
        heartbeat = self._claim_heartbeats.get(asyncio.current_task())
        if heartbeat is not None:
            heartbeat.cancel()
        return await self._mongo.update_download_result(
            collection, record_id, updates, status, claim_token=claim_token, unset_fields=unset_fields,
        )

    async def _load_template(self, name: str, *, refresh: bool = False) -> SiteTemplate | None:
        now = perf_counter()
        if not refresh and name in self._templates and now - self._templates_checked_at.get(name, 0) < settings.download_template_cache_seconds:
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
        self._templates_checked_at[name] = perf_counter()
        return released.template

    @staticmethod
    def _existing_path(record: dict[str, Any], key: str) -> str:
        value = get_nested_value(record, key)
        if isinstance(value, dict):
            value = value.get("url")
        return str(value).strip() if isinstance(value, str) else ""

    async def stop(self) -> None:
        self._running = False
        await self._cancel_downloads()
        await self._changes.close()
        await self._http.close()
        await self._minio.close()
        await self._mongo.close()
        logger.info("DownloadWorker stopped")
