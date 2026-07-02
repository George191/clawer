from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Any
from urllib.parse import urlparse

from minio import Minio
from motor.motor_asyncio import AsyncIOMotorClient
from sqlalchemy import text
import urllib3

from app.config.settings import settings
from app.storage.postgres_client import PostgresClient

logger = logging.getLogger("snapshot_external_news_links")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_MEDIA_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg",
    ".tif", ".tiff", ".avif", ".ico",
    ".mp4", ".m4v", ".mov", ".avi", ".wmv", ".flv", ".webm",
    ".mkv", ".mpeg", ".mpg", ".ts", ".m2ts", ".3gp", ".ogv",
)
_PAGE_EXTENSIONS = (
    ".html", ".htm", ".xhtml", ".shtml", ".shtm",
    ".php", ".phtml", ".php3", ".php4", ".php5",
    ".asp", ".aspx", ".ashx", ".axd",
    ".jsp", ".jspx", ".do", ".action",
    ".cfm", ".cgi",
)

_DEFAULT_COLLECTION = "external_news_link_snapshot"


@dataclass(slots=True)
class LinkCandidate:
    source_record_id: str
    source_data_type: str
    source_data_source: str
    source_page_url: str
    external_url: str
    external_url_norm: str
    external_domain: str


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Snapshot external news links from ts_rds.rds_news into MinIO + Mongo."
    )
    parser.add_argument("--batch-size", type=int, default=500, help="Source news rows per batch")
    parser.add_argument("--record-id", default="", help="Only scan one source record_id")
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrent fetches")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout seconds")
    parser.add_argument("--collection", default=_DEFAULT_COLLECTION, help="Mongo collection name")
    parser.add_argument("--max-content-bytes", type=int, default=2 * 1024 * 1024, help="Max HTML bytes to keep")
    parser.add_argument("--skip-existing", action="store_true", help="Skip Mongo records already downloaded")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser


def _clean_url(url: Any) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if parsed.query:
        clean += f"?{parsed.query}"
    return clean


def _path_extension(url: str) -> str:
    path = (urlparse(url).path or "").rstrip("/").lower()
    if not path:
        return ""
    last_segment = path.rsplit("/", 1)[-1]
    if "." not in last_segment:
        return ""
    return f".{last_segment.rsplit('.', 1)[-1]}"


def _is_page_candidate(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path or ""
    ext = _path_extension(url)
    if ext:
        return ext in _PAGE_EXTENSIONS
    if not path:
        return True
    last_segment = path.rsplit("/", 1)[-1]
    if "." in last_segment:
        return False
    return True


def _is_media_candidate(url: str) -> bool:
    return _path_extension(url) in _MEDIA_EXTENSIONS


def _is_attachment_candidate(url: str) -> bool:
    ext = _path_extension(url)
    if not ext:
        return False
    if ext in _PAGE_EXTENSIONS or ext in _MEDIA_EXTENSIONS:
        return False
    return True


def _normalize_domain(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _snapshot_record_id(source_record_id: str, external_url_norm: str) -> str:
    payload = f"{source_record_id}|{external_url_norm}"
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _object_key(source_data_source: str, source_record_id: str, external_url_norm: str) -> str:
    url_hash = hashlib.md5(external_url_norm.encode("utf-8")).hexdigest()
    ext = _path_extension(external_url_norm) or ".html"
    return (
        f"external_snapshot/news/{source_data_source}/"
        f"{source_record_id}/{url_hash}{ext}"
    )


def _request_headers(url: str) -> dict[str, str]:
    parsed = urlparse(url)
    referer = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else url
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": referer,
    }


async def _load_candidates(pg: PostgresClient, args: argparse.Namespace) -> list[LinkCandidate]:
    rows = await _load_source_rows(pg, args)
    return _extract_candidates(rows)


async def _load_source_rows(
    pg: PostgresClient,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    record_id = str(args.record_id or "").strip()
    if not record_id:
        return []

    sql = """
    SELECT record_id, data_source, data_type, raw_data
    FROM ts_rds.rds_news
    WHERE record_id = :record_id
    """
    return await pg.fetch_all(sql, {"record_id": record_id})


async def _load_source_batch(
    pg: PostgresClient,
    limit: int,
    last_updated_at: Any,
    last_record_id: str,
) -> list[dict[str, Any]]:
    if last_updated_at is None:
        sql = """
        SELECT record_id, data_source, data_type, raw_data, updated_at
        FROM ts_rds.rds_news
        ORDER BY updated_at DESC, record_id DESC
        LIMIT :limit
        """
        params = {"limit": limit}
    else:
        sql = """
        SELECT record_id, data_source, data_type, raw_data, updated_at
        FROM ts_rds.rds_news
        WHERE updated_at < :last_updated_at
           OR (updated_at = :last_updated_at AND record_id < :last_record_id)
        ORDER BY updated_at DESC, record_id DESC
        LIMIT :limit
        """
        params = {
            "last_updated_at": last_updated_at,
            "last_record_id": last_record_id,
            "limit": limit,
        }
    return await pg.fetch_all(sql, params)


def _extract_candidates(rows: list[dict[str, Any]]) -> list[LinkCandidate]:
    candidates: list[LinkCandidate] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        raw_data = row.get("raw_data") or {}
        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except json.JSONDecodeError:
                continue
        if not isinstance(raw_data, dict):
            continue

        source_page_url = _clean_url(raw_data.get("url"))
        external_links = raw_data.get("external_links")
        if not source_page_url or not isinstance(external_links, list):
            continue

        source_record_id = str(row.get("record_id") or "").strip()
        source_data_source = str(row.get("data_source") or "").strip()
        source_data_type = str(row.get("data_type") or "news").strip() or "news"

        for item in external_links:
            external_url_norm = _clean_url(item)
            if not external_url_norm:
                continue
            if _is_media_candidate(external_url_norm):
                continue
            if _is_attachment_candidate(external_url_norm):
                continue
            if not _is_page_candidate(external_url_norm):
                continue
            key = (source_record_id, external_url_norm)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                LinkCandidate(
                    source_record_id=source_record_id,
                    source_data_type=source_data_type,
                    source_data_source=source_data_source,
                    source_page_url=source_page_url,
                    external_url=str(item),
                    external_url_norm=external_url_norm,
                    external_domain=_normalize_domain(external_url_norm),
                )
            )
    return candidates


async def _ensure_mongo_collection(client: AsyncIOMotorClient, collection_name: str):
    db = client[settings.db_name]
    collection = db[collection_name]
    await collection.create_index("snapshot_record_id", unique=True)
    await collection.create_index("source_record_id")
    await collection.create_index("source_data_source")
    await collection.create_index("external_url_norm")
    await collection.create_index("status")
    return collection


async def _fetch_html(
    candidate: LinkCandidate,
    timeout: float,
    max_content_bytes: int,
) -> dict[str, Any]:
    import requests

    def _do_request() -> dict[str, Any]:
        session = requests.Session()
        session.trust_env = False
        try:
            response = session.get(
                candidate.external_url_norm,
                headers=_request_headers(candidate.external_url_norm),
                timeout=timeout,
                verify=False,
                allow_redirects=True,
                stream=True,
            )
        except Exception as exc:
            return {
                "status": "failed",
                "http_status": None,
                "content_type": None,
                "final_url": None,
                "html_bytes": None,
                "error": str(exc),
            }

        content_type = response.headers.get("content-type", "")
        http_status = int(response.status_code)
        final_url = str(response.url)

        if http_status >= 400:
            response.close()
            return {
                "status": "failed",
                "http_status": http_status,
                "content_type": content_type,
                "final_url": final_url,
                "html_bytes": None,
                "error": f"http_{http_status}",
            }

        if "text/html" not in content_type.lower() and "application/xhtml+xml" not in content_type.lower():
            response.close()
            return {
                "status": "skipped",
                "http_status": http_status,
                "content_type": content_type,
                "final_url": final_url,
                "html_bytes": None,
                "error": "non_html_content_type",
            }

        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_content_bytes:
                    response.close()
                    return {
                        "status": "skipped",
                        "http_status": http_status,
                        "content_type": content_type,
                        "final_url": final_url,
                        "html_bytes": None,
                        "error": f"content_too_large:{total}",
                    }
                chunks.append(chunk)
        finally:
            response.close()

        html_bytes = b"".join(chunks)
        if not html_bytes.strip():
            return {
                "status": "skipped",
                "http_status": http_status,
                "content_type": content_type,
                "final_url": final_url,
                "html_bytes": None,
                "error": "empty_body",
            }

        return {
            "status": "downloaded",
            "http_status": http_status,
            "content_type": content_type,
            "final_url": final_url,
            "html_bytes": html_bytes,
            "error": "",
        }

    return await asyncio.to_thread(_do_request)


async def _run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    pg = PostgresClient()
    await pg.connect()

    mongo_client = AsyncIOMotorClient(settings.db_url)
    await mongo_client.admin.command("ping")
    mongo_collection = await _ensure_mongo_collection(mongo_client, str(args.collection))

    minio = Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    if not minio.bucket_exists(settings.minio_bucket):
        minio.make_bucket(settings.minio_bucket)

    try:
        semaphore = asyncio.Semaphore(max(1, int(args.concurrency)))
        stats = {
            "source_rows": 0,
            "candidates": 0,
            "processed": 0,
            "downloaded": 0,
            "failed": 0,
            "skipped": 0,
            "existing": 0,
        }

        async def _process_one(candidate: LinkCandidate) -> None:
            async with semaphore:
                snapshot_record_id = _snapshot_record_id(
                    candidate.source_record_id,
                    candidate.external_url_norm,
                )
                existing = await mongo_collection.find_one(
                    {"snapshot_record_id": snapshot_record_id},
                    {"_id": 0, "status": 1},
                )
                if args.skip_existing and existing and existing.get("status") == "downloaded":
                    stats["existing"] += 1
                    stats["processed"] += 1
                    return

                fetch_result = await _fetch_html(
                    candidate,
                    timeout=float(args.timeout),
                    max_content_bytes=int(args.max_content_bytes),
                )

                html_minio_path = ""
                if fetch_result["status"] == "downloaded" and fetch_result["html_bytes"]:
                    html_minio_path = _object_key(
                        candidate.source_data_source,
                        candidate.source_record_id,
                        candidate.external_url_norm,
                    )
                    minio.put_object(
                        bucket_name=settings.minio_bucket,
                        object_name=html_minio_path,
                        data=BytesIO(fetch_result["html_bytes"]),
                        length=len(fetch_result["html_bytes"]),
                        content_type="text/html; charset=utf-8",
                    )

                now = datetime.now(timezone.utc)
                document = {
                    "snapshot_record_id": snapshot_record_id,
                    "source_record_id": candidate.source_record_id,
                    "source_data_type": candidate.source_data_type,
                    "source_data_source": candidate.source_data_source,
                    "source_page_url": candidate.source_page_url,
                    "external_url": candidate.external_url,
                    "external_url_norm": candidate.external_url_norm,
                    "external_domain": candidate.external_domain,
                    "final_url": fetch_result.get("final_url") or "",
                    "source_url": candidate.external_url_norm,
                    "url": html_minio_path,
                    "html_minio_path": html_minio_path,
                    "status": fetch_result["status"],
                    "http_status": fetch_result.get("http_status"),
                    "content_type": fetch_result.get("content_type") or "",
                    "error": fetch_result.get("error") or "",
                    "fetched_at": now,
                    "_meta": {
                        "created_at": now,
                        "updated_at": now,
                    },
                }

                if existing:
                    previous = await mongo_collection.find_one(
                        {"snapshot_record_id": snapshot_record_id},
                        {"_id": 0, "_meta.created_at": 1},
                    )
                    if previous and previous.get("_meta", {}).get("created_at"):
                        document["_meta"]["created_at"] = previous["_meta"]["created_at"]

                await mongo_collection.replace_one(
                    {"snapshot_record_id": snapshot_record_id},
                    document,
                    upsert=True,
                )

                stats["processed"] += 1
                stats[fetch_result["status"]] += 1

                if stats["processed"] % 50 == 0:
                    logger.info(
                        "Processed=%d downloaded=%d failed=%d skipped=%d existing=%d",
                        stats["processed"],
                        stats["downloaded"],
                        stats["failed"],
                        stats["skipped"],
                        stats["existing"],
                    )

        record_id = str(args.record_id or "").strip()
        if record_id:
            candidates = await _load_candidates(pg, args)
            stats["source_rows"] = 1 if candidates else 0
            stats["candidates"] = len(candidates)
            logger.info(
                "Loaded record batch: record_id=%s candidates=%d",
                record_id,
                len(candidates),
            )
            if candidates:
                await asyncio.gather(*(_process_one(candidate) for candidate in candidates))
        else:
            last_updated_at: Any = None
            last_record_id = ""
            batch_size = max(1, int(args.batch_size))

            while True:
                rows = await _load_source_batch(
                    pg,
                    limit=batch_size,
                    last_updated_at=last_updated_at,
                    last_record_id=last_record_id,
                )
                if not rows:
                    break

                stats["source_rows"] += len(rows)
                last_updated_at = rows[-1].get("updated_at")
                last_record_id = str(rows[-1].get("record_id") or "")

                candidates = _extract_candidates(rows)
                stats["candidates"] += len(candidates)
                logger.info(
                    "Loaded source batch: rows=%d candidates=%d scanned_rows=%d",
                    len(rows),
                    len(candidates),
                    stats["source_rows"],
                )
                if candidates:
                    await asyncio.gather(*(_process_one(candidate) for candidate in candidates))

                if len(rows) < batch_size:
                    break

        logger.info(
            "Completed. source_rows=%d candidates=%d processed=%d downloaded=%d failed=%d skipped=%d existing=%d",
            stats["source_rows"],
            stats["candidates"],
            stats["processed"],
            stats["downloaded"],
            stats["failed"],
            stats["skipped"],
            stats["existing"],
        )
        return 0
    finally:
        await pg.close()
        mongo_client.close()


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
