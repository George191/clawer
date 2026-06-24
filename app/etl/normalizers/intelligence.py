from __future__ import annotations

from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import resolve_asset_link, safe_datetime, safe_str


def normalize_planet(record: dict[str, Any]) -> dict[str, Any]:
    meta = record.get("_meta", {}) or {}
    assets = record.get("assets") or {}
    data_source = safe_str(meta.get("data_source") or meta.get("template")) or "planet"
    record_id = safe_str(meta.get("record_id") or record.get("record_id") or record.get("url") or record.get("name")) or ""

    return {
        "data_source": data_source,
        "data_type": "intelligence",
        "record_id": record_id,
        "title": safe_str(record.get("title")),
        "url": (
            resolve_asset_link(assets, "url", "original_file", "file", "pdf")
            or safe_str(record.get("original_file") or record.get("url"))
        ),
        "source_published_at": safe_datetime(record.get("date")),
        "source_updated_at": safe_datetime(record.get("modified")),
        "summary": safe_str(record.get("summary") or record.get("description")),
        "file_name": safe_str(record.get("name")),
        "file_size": safe_str(record.get("size")),
        "file_type": safe_str(record.get("file_type") or "pdf"),
    }


register_normalizer("intelligence", "planet", normalize_planet)
