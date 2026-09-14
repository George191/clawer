from __future__ import annotations

import json
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import apply_asset_path_overrides, safe_datetime, safe_str
from app.storage.minio_client import _guess_content_type


def _meta(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("_meta")
    return value if isinstance(value, dict) else {}


def normalize_planet(record: dict[str, Any]) -> dict[str, Any]:

    def pick(name: str) -> Any:
        return record.get(name)

    def dump(value: Any) -> str | None:
        return json.dumps(value, ensure_ascii=False) if value is not None else None
    
    meta = _meta(record)
    record, _ = apply_asset_path_overrides(record)

    minio_path = safe_str(pick("url"))
    file_type = (
        _guess_content_type(minio_path)
        if minio_path else None
    )
    data_source = safe_str(meta.get("template"))
    record_id = safe_str(meta.get("record_id"))

    return {
        "data_source": data_source,
        "data_type": safe_str(pick("data_type")),
        "record_id": record_id,
        "title": safe_str(pick("title")),
        "url": minio_path,
        "source_published_at": None,
        "summary": None,
        "source_updated_at": safe_datetime(pick("modified")),
        "file_name": safe_str(pick("name")),
        "file_size": safe_str(pick("size")),
        "file_type": file_type,
    }


register_normalizer("intelligence", "planet", normalize_planet)
