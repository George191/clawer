from __future__ import annotations

from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import apply_asset_path_overrides, safe_datetime, safe_str
from app.storage.minio_client import get_minio_client


async def normalize_planet(record: dict[str, Any]) -> dict[str, Any]:
    record, _ = apply_asset_path_overrides(record)
    meta = record.get("_meta", {}) or {}
    minio_path = safe_str(record.get("url"))
    file_type = (
        await get_minio_client().get_object_content_type(minio_path)
        if minio_path else None
    )
    data_source = safe_str(meta.get("template"))
    record_id = safe_str(meta.get("record_id"))

    return {
        "data_source": data_source,
        "data_type": "intelligence",
        "record_id": record_id,
        "title": safe_str(record.get("title")),
        "url": minio_path,
        "source_updated_at": safe_datetime(record.get("modified")),
        "file_name": safe_str(record.get("name")),
        "file_size": safe_str(record.get("size")),
        "file_type": file_type,
    }


register_normalizer("intelligence", "planet", normalize_planet)
