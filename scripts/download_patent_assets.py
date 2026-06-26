"""Standalone Google Patent asset downloader.

This file is intentionally self-contained so it can be copied to a server and
run without importing this repository. It preserves the current downloader flow
for Google Patent records:

- read Mongo records with `_meta.download_status` in `pending/downloading`;
- extract `patent.pdf`, `patent.figures`, and `patent.thumbnail`;
- download bytes with retry and size limits;
- upload to MinIO under `patent/google_patent/{record_id}/{filename}`;
- update Mongo asset fields and `_meta.download_status`.

Required third-party packages on the server:

    pip install pymongo minio
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import mimetypes
import os
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

try:
    from minio import Minio
    from pymongo import MongoClient
except ImportError as exc:  # pragma: no cover - operator-facing import guard
    raise SystemExit(
        "Missing dependency. Install on the server with: pip install pymongo minio"
    ) from exc


logger = logging.getLogger("patent_downloader")

TEMPLATE_NAME = "google_patent"
DATA_TYPE = "patent"
URL_PREFIX = "https://patentimages.storage.googleapis.com/"
RETRY_INITIAL_DELAY = 1.0
RETRY_MAX_DELAY = 60.0
RETRY_ALERT_THRESHOLD = 10
RETRY_CRITICAL_THRESHOLD = 50
DOWNLOAD_SELECTORS = (
    {
        "selector": "patent.pdf",
        "file_extension": "pdf",
        "url_prefix": URL_PREFIX,
    },
    {
        "selector": "patent.figures",
        "file_extension": "png",
        "url_prefix": URL_PREFIX,
    },
    {
        "selector": "patent.thumbnail",
        "file_extension": "png",
        "url_prefix": URL_PREFIX,
    },
)


@dataclass(frozen=True)
class Settings:
    db_url: str
    db_name: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    minio_secure: bool
    template: str
    batch_size: int
    workers: int
    poll_seconds: float
    max_batches: int
    watch: bool
    once: bool
    max_file_size: int
    timeout: float
    mongo_timeout_ms: int
    max_retries: int
    verify_ssl: bool
    user_agent: str
    dry_run: bool


@dataclass(frozen=True)
class DownloadItem:
    url: str
    filename: str
    asset_key: str


class FileTooLargeError(Exception):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "y", "on"}


def _load_env_file(path: str | None) -> None:
    if not path:
        return
    env_path = Path(path)
    if not env_path.exists():
        raise FileNotFoundError(f"env file not found: {env_path}")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [PATENT-DOWNLOADER] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def _get_nested_value(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if current is None:
            return None
    return current


def _extract_url_from_dict(data: dict[str, Any], url_prefix: str) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    for key in ("href", "src", "url", "link", "full", "thumbnail", "pdf"):
        value = data.get(key)
        if not value:
            continue
        text = str(value)
        results.append((key, url_prefix + text if url_prefix else text))
    return results


def _make_filename(url: str, file_ext: str | None = None, suffix: str = "") -> str:
    if file_ext:
        ext = file_ext.lstrip(".")
    else:
        path_part = url.split("?", 1)[0]
        tail = path_part.rsplit("/", 1)[-1]
        ext = tail.rsplit(".", 1)[-1].lower() if "." in tail else "bin"

    name_part = url.split("?", 1)[0].rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if not name_part or len(name_part) > 60:
        name_part = hashlib.md5(url.encode()).hexdigest()[:12]

    return f"{name_part}{suffix}.{ext}"


def _extract_download_items(record: dict[str, Any]) -> list[DownloadItem]:
    items: list[DownloadItem] = []
    for config in DOWNLOAD_SELECTORS:
        selector = config["selector"]
        url_prefix = config["url_prefix"]
        file_ext = config["file_extension"]
        raw_value = _get_nested_value(record, selector)

        if raw_value is None:
            continue

        if isinstance(raw_value, list):
            for index, item in enumerate(raw_value):
                if isinstance(item, dict):
                    for field_name, sub_url in _extract_url_from_dict(item, url_prefix):
                        items.append(
                            DownloadItem(
                                url=sub_url,
                                filename=_make_filename(sub_url, file_ext, suffix=f"_{index:05d}"),
                                asset_key=f"assets.{selector}.{index}.{field_name}",
                            )
                        )
                elif isinstance(item, str):
                    full_url = item if item.startswith("http") else url_prefix + item
                    items.append(
                        DownloadItem(
                            url=full_url,
                            filename=_make_filename(full_url, file_ext, suffix=f"_{index:05d}"),
                            asset_key=f"assets.{selector}.{index}",
                        )
                    )
            continue

        if isinstance(raw_value, dict):
            for field_name, sub_url in _extract_url_from_dict(raw_value, url_prefix):
                items.append(
                    DownloadItem(
                        url=sub_url,
                        filename=_make_filename(sub_url, file_ext),
                        asset_key=f"assets.{selector}.{field_name}",
                    )
                )
            continue

        value = str(raw_value)
        full_url = value if value.startswith("http") else url_prefix + value
        items.append(
            DownloadItem(
                url=full_url,
                filename=_make_filename(full_url, file_ext),
                asset_key=f"assets.{selector}",
            )
        )

    return items


def _content_type(filename: str) -> str:
    content_type = mimetypes.guess_type(filename)[0]
    if content_type:
        return content_type
    ext = Path(filename).suffix.lower()
    fallback = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
    }
    return fallback.get(ext, "application/octet-stream")


def _http_status(exc: Exception) -> int | None:
    if isinstance(exc, HTTPError):
        return exc.code
    return getattr(exc, "status_code", None)


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, FileTooLargeError):
        return False
    return _http_status(exc) not in (403, 404)


def _download_bytes(settings: Settings, url: str) -> bytes | None:
    retry_count = 0
    delay = RETRY_INITIAL_DELAY
    ssl_context = None if settings.verify_ssl else ssl._create_unverified_context()

    while True:
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": settings.user_agent,
                    "Accept": "*/*",
                },
            )
            with urlopen(request, timeout=settings.timeout, context=ssl_context) as response:
                data = response.read(settings.max_file_size + 1)
            if len(data) > settings.max_file_size:
                raise FileTooLargeError(
                    f"file size exceeds limit {settings.max_file_size}: {url}"
                )
            if retry_count > 0:
                logger.info("Succeeded after %d retries: %s", retry_count, url)
            return data
        except Exception as exc:
            if not _is_retryable(exc):
                status = _http_status(exc)
                if status == 403:
                    logger.warning("403 access denied, skipping: %s", url)
                elif status == 404:
                    logger.warning("404 not found, skipping: %s", url)
                else:
                    logger.warning("Non-retryable error, skipping: %s (%s)", url, exc)
                return None

            retry_count += 1
            logger.warning(
                "Retry %d for %s | error=%s | status=%s | time=%s",
                retry_count,
                url,
                type(exc).__name__,
                _http_status(exc),
                datetime.now(timezone.utc).isoformat(),
            )
            if retry_count >= settings.max_retries:
                logger.warning(
                    "Reached max retries (%d), skipping: %s",
                    settings.max_retries,
                    url,
                )
                return None
            if retry_count % RETRY_CRITICAL_THRESHOLD == 0:
                logger.error(
                    "Critical retry alert: %d consecutive retries for %s",
                    retry_count,
                    url,
                )
            elif retry_count % RETRY_ALERT_THRESHOLD == 0:
                logger.warning(
                    "Retry alert: %d consecutive retries for %s",
                    retry_count,
                    url,
                )
            time.sleep(delay)
            delay = min(delay * 2, RETRY_MAX_DELAY)


class PatentDownloader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.mongo = MongoClient(
            settings.db_url,
            serverSelectionTimeoutMS=settings.mongo_timeout_ms,
            connectTimeoutMS=settings.mongo_timeout_ms,
        )
        self.db = self.mongo[settings.db_name]
        self.collection = self.db[settings.template]
        self.minio = Minio(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    def close(self) -> None:
        self.mongo.close()

    def ensure_minio_bucket(self) -> None:
        if self.settings.dry_run:
            return
        if not self.minio.bucket_exists(self.settings.minio_bucket):
            self.minio.make_bucket(self.settings.minio_bucket)
            logger.info("Created MinIO bucket: %s", self.settings.minio_bucket)

    def pending_count(self) -> int:
        return self.collection.count_documents(
            {"_meta.download_status": {"$in": ["pending", "downloading"]}}
        )

    def fetch_pending(self) -> list[dict[str, Any]]:
        cursor = self.collection.find(
            {"_meta.download_status": {"$in": ["pending", "downloading"]}}
        ).limit(self.settings.batch_size)
        return list(cursor)

    def update_status(self, record_id: str, status: str) -> None:
        if self.settings.dry_run:
            logger.info("[dry-run] status %s -> %s", record_id, status)
            return
        self.collection.update_one(
            {"_meta.record_id": record_id},
            {
                "$set": {
                    "_meta.download_status": status,
                    "_meta.updated_at": datetime.now(timezone.utc),
                }
            },
        )

    def update_success(self, record_id: str, updates: dict[str, str]) -> None:
        if self.settings.dry_run:
            logger.info("[dry-run] update %s fields=%s", record_id, sorted(updates))
            return
        payload: dict[str, Any] = {
            **updates,
            "_meta.download_status": "downloaded",
            "_meta.updated_at": datetime.now(timezone.utc),
        }
        self.collection.update_one({"_meta.record_id": record_id}, {"$set": payload})

    def upload_asset(self, record_id: str, item: DownloadItem, data: bytes) -> str:
        object_key = f"{DATA_TYPE}/{self.settings.template}/{record_id}/{item.filename}"
        if self.settings.dry_run:
            logger.info("[dry-run] upload %s bytes=%d", object_key, len(data))
            return object_key
        self.minio.put_object(
            bucket_name=self.settings.minio_bucket,
            object_name=object_key,
            data=BytesIO(data),
            length=len(data),
            content_type=_content_type(item.filename),
        )
        return object_key

    def process_record(self, record: dict[str, Any]) -> bool:
        meta = record.get("_meta") or {}
        record_id = meta.get("record_id")
        if not record_id:
            logger.warning("Skipping record with missing _meta.record_id")
            return False

        try:
            items = _extract_download_items(record)
            if not items:
                self.update_status(record_id, "no_assets")
                return True

            updates: dict[str, str] = {}
            for item in items:
                data = _download_bytes(self.settings, item.url)
                if data is None:
                    continue
                object_key = self.upload_asset(record_id, item, data)
                updates[item.asset_key] = object_key

            if updates:
                self.update_success(record_id, updates)
                logger.info("Downloaded %d assets for %s", len(updates), record_id)
            else:
                self.update_status(record_id, "no_assets")
            return True
        except Exception:
            logger.exception("Failed for record_id=%s", record_id)
            self.update_status(record_id, "failed")
            return False

    def run_batch(self) -> int:
        records = self.fetch_pending()
        if not records:
            return 0
        logger.info("Found %d pending downloads", len(records))
        success = 0
        with ThreadPoolExecutor(max_workers=self.settings.workers) as executor:
            futures = [executor.submit(self.process_record, record) for record in records]
            for future in as_completed(futures):
                if future.result() is True:
                    success += 1
        logger.info("Completed %d/%d records", success, len(records))
        return success

    def run(self) -> None:
        self.ensure_minio_bucket()
        batches = 0
        while True:
            success = self.run_batch()
            batches += 1
            pending = self.pending_count()
            logger.info("Batch=%d success=%d pending=%d", batches, success, pending)

            if self.settings.once:
                break
            if not self.settings.watch and pending == 0:
                break
            if self.settings.max_batches > 0 and batches >= self.settings.max_batches:
                break
            if success == 0:
                time.sleep(self.settings.poll_seconds)


def _required(value: str, name: str) -> str:
    if not value:
        raise SystemExit(f"Missing required config: {name}")
    return value


def _build_settings(args: argparse.Namespace) -> Settings:
    return Settings(
        db_url=_required(args.db_url or _env("SPIDER_DB_URL"), "--db-url/SPIDER_DB_URL"),
        db_name=_required(args.db_name or _env("SPIDER_DB_NAME"), "--db-name/SPIDER_DB_NAME"),
        minio_endpoint=_required(
            args.minio_endpoint or _env("SPIDER_MINIO_ENDPOINT"),
            "--minio-endpoint/SPIDER_MINIO_ENDPOINT",
        ),
        minio_access_key=_required(
            args.minio_access_key or _env("SPIDER_MINIO_ACCESS_KEY"),
            "--minio-access-key/SPIDER_MINIO_ACCESS_KEY",
        ),
        minio_secret_key=_required(
            args.minio_secret_key or _env("SPIDER_MINIO_SECRET_KEY"),
            "--minio-secret-key/SPIDER_MINIO_SECRET_KEY",
        ),
        minio_bucket=_required(
            args.minio_bucket or _env("SPIDER_MINIO_BUCKET"),
            "--minio-bucket/SPIDER_MINIO_BUCKET",
        ),
        minio_secure=_parse_bool(args.minio_secure, _parse_bool(_env("SPIDER_MINIO_SECURE"))),
        template=args.template,
        batch_size=args.batch,
        workers=args.workers,
        poll_seconds=args.poll,
        max_batches=args.max_batches,
        watch=args.watch,
        once=args.once,
        max_file_size=args.max_file_size,
        timeout=args.timeout,
        mongo_timeout_ms=args.mongo_timeout_ms,
        max_retries=args.max_retries,
        verify_ssl=args.verify_ssl,
        user_agent=args.user_agent,
        dry_run=args.dry_run,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Standalone Google Patent asset downloader",
    )
    parser.add_argument("--env-file", help="Optional dotenv-style config file")
    parser.add_argument("--db-url", help="MongoDB URL; defaults to SPIDER_DB_URL")
    parser.add_argument("--db-name", help="MongoDB database; defaults to SPIDER_DB_NAME")
    parser.add_argument("--minio-endpoint", help="MinIO endpoint; defaults to SPIDER_MINIO_ENDPOINT")
    parser.add_argument("--minio-access-key", help="MinIO access key")
    parser.add_argument("--minio-secret-key", help="MinIO secret key")
    parser.add_argument("--minio-bucket", help="MinIO bucket")
    parser.add_argument("--minio-secure", choices=("true", "false"), help="Use HTTPS for MinIO")
    parser.add_argument("--template", default=TEMPLATE_NAME, help="Mongo collection/template name")
    parser.add_argument("--batch", type=int, default=50, help="Records per batch")
    parser.add_argument("--workers", type=int, default=5, help="Concurrent record workers")
    parser.add_argument("--poll", type=float, default=10.0, help="Sleep seconds when idle/no progress")
    parser.add_argument("--max-batches", type=int, default=0, help="0 means no batch limit")
    parser.add_argument("--once", action="store_true", help="Process one batch and exit")
    parser.add_argument("--watch", action="store_true", help="Keep polling after queue becomes empty")
    parser.add_argument("--dry-run", action="store_true", help="Do not write Mongo or MinIO")
    parser.add_argument("--timeout", type=float, default=300.0, help="Per-file HTTP timeout")
    parser.add_argument(
        "--mongo-timeout-ms",
        type=int,
        default=5000,
        help="MongoDB connection timeout in milliseconds",
    )
    parser.add_argument("--max-retries", type=int, default=10, help="Max retries per asset")
    parser.add_argument(
        "--max-file-size",
        type=int,
        default=500 * 1024 * 1024,
        help="Max file size in bytes",
    )
    parser.add_argument(
        "--verify-ssl",
        action="store_true",
        help="Verify HTTPS certificates for downloads; default matches current downloader false",
    )
    parser.add_argument(
        "--user-agent",
        default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        help="HTTP User-Agent",
    )
    args = parser.parse_args()

    _load_env_file(args.env_file)
    _setup_logging()
    settings = _build_settings(args)

    logger.info("=== Standalone Patent Downloader Starting ===")
    logger.info("  MongoDB:  %s/%s", settings.db_url, settings.db_name)
    logger.info("  MinIO:    %s/%s", settings.minio_endpoint, settings.minio_bucket)
    logger.info("  Template: %s", settings.template)
    logger.info("  Dry run:  %s", settings.dry_run)
    logger.info("=" * 40)

    downloader = PatentDownloader(settings)
    try:
        downloader.run()
    finally:
        downloader.close()


if __name__ == "__main__":
    main()
