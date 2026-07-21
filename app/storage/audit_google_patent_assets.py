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

from app.config.settings import settings

TEMPLATE_NAME = "google_patent"
DEFAULT_LIMIT = 1000
STAT_CONCURRENCY = 20
MISSING_ERROR_CODES = {"NoSuchKey", "NoSuchObject"}


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


async def object_exists(client: Minio, bucket: str, object_key: str) -> bool:
    try:
        await asyncio.to_thread(client.stat_object, bucket, object_key)
        return True
    except S3Error as exc:
        if exc.code in MISSING_ERROR_CODES:
            return False
        raise


async def audit(limit: int, skip: int = 0, mark_pending: bool = False) -> int:
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
            },
        ).skip(skip).limit(limit)

        records: list[dict[str, Any]] = []
        references_by_key: dict[str, list[tuple[str, str]]] = {}
        async for document in cursor:
            meta = document.get("_meta") or {}
            patent = document.get("patent") or {}
            record_id = str(meta.get("record_id") or document.get("_id"))
            publication_number = str(patent.get("publication_number") or "-")
            records.append(document)

            for asset_path, stored_value in iter_asset_values(document.get("assets")):
                object_key = normalize_object_key(stored_value, settings.minio_bucket)
                references_by_key.setdefault(object_key, []).append(
                    (record_id, f"{publication_number} | {asset_path}")
                )

        semaphore = asyncio.Semaphore(STAT_CONCURRENCY)

        async def check(key: str) -> tuple[str, bool]:
            async with semaphore:
                return key, await object_exists(minio, settings.minio_bucket, key)

        checks = await asyncio.gather(*(check(key) for key in references_by_key))
        missing_keys = {key for key, exists in checks if not exists}
        missing_records: set[str] = set()
        missing_references = 0

        print("\n缺失的 MinIO 资源：")
        if not missing_keys:
            print("  无")
        else:
            for object_key in sorted(missing_keys):
                for record_id, label in references_by_key[object_key]:
                    missing_records.add(record_id)
                    missing_references += 1
                    print(f"  record_id={record_id} | {label} | key={object_key}")

        total_references = sum(len(items) for items in references_by_key.values())
        print("\n统计：")
        print(f"  扫描记录数：{len(records)}（上限 {limit}）")
        print(f"  assets 资源引用数：{total_references}")
        print(f"  MinIO 唯一对象数：{len(references_by_key)}")
        print(f"  缺失资源引用数：{missing_references}")
        print(f"  缺失唯一对象数：{len(missing_keys)}")
        print(f"  受影响记录数：{len(missing_records)}")
        if mark_pending and missing_records:
            result = await collection.update_many(
                {"_meta.record_id": {"$in": sorted(missing_records)}},
                {
                    "$set": {
                        "_meta.download_status": "pending",
                        "_meta.sync_status": "pending",
                        "_meta.updated_at": datetime.now(timezone.utc),
                    },
                    "$unset": {"_meta.download_claim_token": ""},
                },
            )
            print(f"  已重置为 pending：{result.modified_count} 条记录")
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
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit 必须大于 0")
    if args.skip < 0:
        parser.error("--skip 不能小于 0")
    return args


def main() -> None:
    args = parse_args()
    asyncio.run(audit(args.limit, args.skip, args.mark_pending))


if __name__ == "__main__":
    main()
