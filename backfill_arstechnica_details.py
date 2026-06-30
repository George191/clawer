from __future__ import annotations

import argparse
import asyncio
import logging
import os
from typing import Any

from app.adapters.arstechnica import ArsTechnicaAdapter
from app.config.settings import settings
from app.downloader.http_client import HttpClient
from app.engine.template_loader import TemplateLoader
from app.storage.mongo_storage import MongoStorage

logger = logging.getLogger("arstechnica_detail_backfill")

_REQUIRED_FIELDS = (
    "author",
    "source_published_at",
    "content_html",
)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill missing Ars Technica detail fields in MongoDB."
    )
    parser.add_argument("--limit", type=int, default=0, help="Maximum records to backfill, 0 means no limit")
    parser.add_argument("--batch-size", type=int, default=100, help="Mongo batch size per round")
    parser.add_argument("--record-id", default="", help="Backfill only this record_id")
    parser.add_argument("--sleep", type=float, default=3.0, help="Seconds between detail requests")
    parser.add_argument("--use-proxy", action="store_true", help="Enable anti-crawl proxy path for detail requests")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser


def _missing_detail_query() -> dict[str, Any]:
    return {
        "$or": [
            {field: {"$exists": False}}
            for field in _REQUIRED_FIELDS
        ] + [
            {field: None}
            for field in _REQUIRED_FIELDS
        ] + [
            {field: ""}
            for field in _REQUIRED_FIELDS
        ],
    }


async def _load_target_by_record_id(
    storage: MongoStorage,
    record_id: str,
) -> list[dict[str, Any]]:
    collection = await storage._get_collection("arstechnica")
    doc = await collection.find_one({"_meta.record_id": record_id})
    if not doc:
        return []
    doc.pop("_id", None)
    return [doc]


async def _load_batch(
    storage: MongoStorage,
    batch_size: int,
) -> list[dict[str, Any]]:
    collection = await storage._get_collection("arstechnica")
    cursor = collection.find(_missing_detail_query()).sort("date", -1).limit(batch_size)
    docs: list[dict[str, Any]] = []
    async for doc in cursor:
        doc.pop("_id", None)
        docs.append(doc)
    return docs


async def _run(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )

    settings.anti_crawl_enabled = bool(args.use_proxy)
    if not args.use_proxy:
        os.environ["ANTI_CRAWL_ENABLED"] = "false"

    loader = TemplateLoader()
    template = loader.load("arstechnica")
    client = HttpClient()
    storage = MongoStorage()
    adapter = ArsTechnicaAdapter(template.base_url, client)

    await adapter.on_before_crawl(template)
    try:
        record_id = str(args.record_id or "").strip()
        limit = int(args.limit)
        batch_size = int(args.batch_size)
        processed = 0
        updated = 0

        while True:
            if record_id:
                targets = await _load_target_by_record_id(storage, record_id)
            else:
                remaining = batch_size
                if limit > 0:
                    remaining = min(batch_size, limit - processed)
                if remaining <= 0:
                    break
                targets = await _load_batch(storage, remaining)

            if not targets:
                if processed == 0:
                    logger.info("No matching Ars Technica records need detail backfill.")
                break

            logger.info(
                "Loaded batch: size=%d processed=%d updated=%d use_proxy=%s",
                len(targets),
                processed,
                updated,
                bool(args.use_proxy),
            )

            for doc in targets:
                current_record_id = str(doc.get("_meta", {}).get("record_id") or "")
                url = str(doc.get("url") or "").strip()
                if not current_record_id or not url:
                    processed += 1
                    if record_id:
                        break
                    continue

                before = {
                    field: doc.get(field)
                    for field in (
                        "author",
                        "source_published_at",
                        "source_updated_at",
                        "content_html",
                        "content",
                        "thumbnail",
                        "images",
                        "attachments",
                        "external_links",
                        "category_names",
                        "tags",
                    )
                }
                payload = {key: value for key, value in doc.items() if key != "_meta"}
                enriched = await adapter._enrich_detail(payload)

                updates = {
                    field: enriched.get(field)
                    for field in (
                        "author",
                        "source_published_at",
                        "source_updated_at",
                        "content_html",
                        "content",
                        "thumbnail",
                        "images",
                        "attachments",
                        "external_links",
                        "category_names",
                        "tags",
                    )
                    if enriched.get(field) and enriched.get(field) != before.get(field)
                }
                if updates:
                    await storage.update_record_fields("arstechnica", current_record_id, updates)
                    updated += 1

                processed += 1
                logger.info(
                    "[%d] record_id=%s updated=%s missing_before=%s",
                    processed,
                    current_record_id,
                    bool(updates),
                    [field for field in _REQUIRED_FIELDS if not before.get(field)],
                )

                if record_id:
                    break
                if limit > 0 and processed >= limit:
                    break
                if args.sleep > 0:
                    await asyncio.sleep(float(args.sleep))

            if record_id:
                break
            if limit > 0 and processed >= limit:
                break

        logger.info("Backfill complete. Updated %d/%d records.", updated, processed)
        return 0
    finally:
        await adapter.close()
        if hasattr(storage, "close"):
            await storage.close()
        await client.close()


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
