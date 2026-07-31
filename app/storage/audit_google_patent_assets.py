"""核对 MongoDB 中的 Google Patent 资源是否仍存在于 MinIO。

运行方式：
    python -m app.storage.audit_google_patent_assets
    python -m app.storage.audit_google_patent_assets --limit 1000

脚本默认只读，结果仅输出到终端；传入 --mark-pending 时会重置缺失记录的状态。
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlparse

from minio import Minio
from minio.error import S3Error
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

from app.config.settings import settings
from app.utils.path import get_nested_value

TEMPLATE_NAME = "google_patent"
DEFAULT_LIMIT = 1000
STAT_CONCURRENCY = 20
MISSING_ERROR_CODES = {"NoSuchKey", "NoSuchObject"}
SOURCE_URL_FIELDS = ("href", "src", "url", "link", "full", "thumbnail", "pdf")


def iter_asset_values(
    value: Any,
    path: tuple[str, ...] = ("assets",),
) -> Iterator[tuple[str, str]]:
    """递归产出 assets 中的字符串值及其字段路径。"""
    if isinstance(value, str):
        object_key = value.strip()
        if object_key:
            yield ".".join(path), object_key
        return

    if isinstance(value, dict):
        for key, child in value.items():
            yield from iter_asset_values(child, (*path, str(key)))
        return

    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_asset_values(child, (*path, str(index)))


def normalize_object_key(value: str, bucket: str) -> str:
    """兼容 assets 中保存相对 key 或完整 MinIO URL 的情况。"""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return value.lstrip("/")

    path = unquote(parsed.path).lstrip("/")
    bucket_prefix = f"{bucket}/"
    return path[len(bucket_prefix):] if path.startswith(bucket_prefix) else path


def has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def iter_expected_asset_paths(document: dict[str, Any]) -> Iterator[str]:
    """Yield asset paths expected from the Google Patent source fields."""
    patent = document.get("patent")
    if not isinstance(patent, dict):
        return

    for field_name in ("pdf", "thumbnail"):
        if has_value(patent.get(field_name)):
            yield f"assets.patent.{field_name}"

    figures = patent.get("figures")
    if isinstance(figures, list):
        for index, item in enumerate(figures):
            if isinstance(item, dict):
                for field_name in SOURCE_URL_FIELDS:
                    if has_value(item.get(field_name)):
                        yield f"assets.patent.figures.{index}.{field_name}"
            elif has_value(item):
                yield f"assets.patent.figures.{index}"
        return

    if isinstance(figures, dict):
        for field_name in SOURCE_URL_FIELDS:
            if has_value(figures.get(field_name)):
                yield f"assets.patent.figures.{field_name}"


async def object_exists(client: Minio, bucket: str, object_key: str) -> bool:
    try:
        await asyncio.to_thread(client.stat_object, bucket, object_key)
        return True
    except S3Error as exc:
        if exc.code in MISSING_ERROR_CODES:
            return False
        raise


def build_repair_update(missing_asset_paths: set[str]) -> dict[str, Any]:
    unset_fields = {"_meta.download_claim_token": ""}
    unset_fields.update(dict.fromkeys(sorted(missing_asset_paths), ""))
    return {
        "$set": {
            "_meta.download_status": "pending",
            "_meta.sync_status": "pending",
            "_meta.updated_at": datetime.now(timezone.utc),
        },
        "$unset": unset_fields,
    }


async def audit(
    limit: int,
    skip: int = 0,
    mark_pending: bool = False,
    summary_only: bool = False,
) -> int:
    if not settings.db_url or not settings.db_name:
        raise RuntimeError("MongoDB 配置不完整：请设置 SPIDER_DB_URL 和 SPIDER_DB_NAME")
    if not settings.minio_endpoint or not settings.minio_bucket:
        raise RuntimeError(
            "MinIO 配置不完整：请设置 SPIDER_MINIO_ENDPOINT 和 SPIDER_MINIO_BUCKET"
        )

    mongo = AsyncIOMotorClient(settings.db_url, serverSelectionTimeoutMS=10_000)
    minio = Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )

    try:
        await mongo.admin.command("ping")
        bucket_exists = await asyncio.to_thread(minio.bucket_exists, settings.minio_bucket)
        if not bucket_exists:
            raise RuntimeError(f"MinIO bucket 不存在：{settings.minio_bucket}")

        collection = mongo[settings.db_name][TEMPLATE_NAME]
        cursor = collection.find(
            {"assets": {"$exists": True, "$nin": [None, {}, []]}},
            {
                "assets": 1,
                "_meta.record_id": 1,
                "patent.publication_number": 1,
                "patent.pdf": 1,
                "patent.thumbnail": 1,
                "patent.figures": 1,
            },
        ).skip(skip).limit(limit)

        records: list[dict[str, Any]] = []
        references_by_key: dict[str, list[tuple[str, str]]] = {}
        missing_asset_records: set[str] = set()
        missing_asset_references = 0
        unexpected_asset_references = 0
        async for document in cursor:
            meta = document.get("_meta") or {}
            patent = document.get("patent") or {}
            record_id = str(meta.get("record_id") or document.get("_id"))
            publication_number = str(patent.get("publication_number") or "-")
            records.append(document)

            actual_asset_paths = set()
            for asset_path, stored_value in iter_asset_values(document.get("assets")):
                actual_asset_paths.add(asset_path)
                object_key = normalize_object_key(stored_value, settings.minio_bucket)
                references_by_key.setdefault(object_key, []).append(
                    (record_id, f"{publication_number} | {asset_path}")
                )

            expected_asset_paths = set(iter_expected_asset_paths(document))
            missing_asset_paths = [
                path
                for path in sorted(expected_asset_paths)
                if not has_value(get_nested_value(document, path))
            ]
            if missing_asset_paths:
                missing_asset_records.add(record_id)
                missing_asset_references += len(missing_asset_paths)
                if not summary_only:
                    print(
                        "source-url-without-asset "
                        f"record_id={record_id} publication={publication_number} "
                        f"paths={','.join(missing_asset_paths)}"
                    )

            unexpected_asset_references += len(actual_asset_paths - expected_asset_paths)

        semaphore = asyncio.Semaphore(STAT_CONCURRENCY)

        async def check(key: str) -> tuple[str, bool]:
            async with semaphore:
                return key, await object_exists(minio, settings.minio_bucket, key)

        checks = await asyncio.gather(*(check(key) for key in references_by_key))
        missing_keys = {key for key, exists in checks if not exists}
        missing_records: set[str] = set()
        missing_asset_paths_by_record: dict[str, set[str]] = {}
        missing_references = 0

        if not summary_only:
            print("\nMissing MinIO objects:")
        if not missing_keys:
            if not summary_only:
                print("  none")
        else:
            for object_key in sorted(missing_keys):
                for record_id, label in references_by_key[object_key]:
                    missing_records.add(record_id)
                    missing_references += 1
                    asset_path = label.rsplit(" | ", 1)[-1]
                    missing_asset_paths_by_record.setdefault(record_id, set()).add(
                        asset_path
                    )
                    if not summary_only:
                        print(f"  record_id={record_id} | {label} | key={object_key}")

        total_references = sum(len(items) for items in references_by_key.values())
        repair_record_ids = missing_records | missing_asset_records
        print("\nSummary:")
        print(f"  scanned_records={len(records)} limit={limit} skip={skip}")
        print(f"  asset_references={total_references}")
        print(f"  unique_minio_objects={len(references_by_key)}")
        print(f"  missing_minio_references={missing_references}")
        print(f"  missing_minio_objects={len(missing_keys)}")
        print(f"  missing_minio_records={len(missing_records)}")
        print(f"  source_url_without_asset_references={missing_asset_references}")
        print(f"  source_url_without_asset_records={len(missing_asset_records)}")
        print(f"  asset_without_source_url_references={unexpected_asset_references}")
        print(f"  repair_candidate_records={len(repair_record_ids)}")
        if mark_pending and repair_record_ids:
            operations = [
                UpdateOne(
                    {"_meta.record_id": record_id},
                    build_repair_update(
                        missing_asset_paths_by_record.get(record_id, set())
                    ),
                )
                for record_id in sorted(repair_record_ids)
            ]
            result = await collection.bulk_write(operations, ordered=False)
            print(f"  marked_pending={result.modified_count}")
        return len(missing_keys)
    finally:
        mongo.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="统计 Mongo Google Patent assets 中在 MinIO 不存在的对象"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"最多扫描的 Mongo 记录数（默认：{DEFAULT_LIMIT}）",
    )
    parser.add_argument("--skip", type=int, default=0, help="跳过前 N 条记录（默认：0）")
    parser.add_argument(
        "--mark-pending",
        action="store_true",
        help="将缺失 MinIO 对象的记录 download/sync 状态重置为 pending",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="只输出汇总统计，不打印逐条缺失明细",
    )
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit 必须大于 0")
    if args.skip < 0:
        parser.error("--skip 不能小于 0")
    return args


def main() -> None:
    args = parse_args()
    asyncio.run(audit(args.limit, args.skip, args.mark_pending, args.summary_only))


if __name__ == "__main__":
    main()
