"""ODS 标准化工具 — 基础函数。

提供各类型/源标准化器的公共依赖：
- safe_str / safe_date / _pick_first  类型安全的值提取
- _extract_asset_paths              从 RDS 原始数据中提取 MinIO 资源路径
- _normalize_generic                通用兜底标准化器
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any


def safe_str(val: Any) -> str | None:
    if val is None:
        return None
    return str(val).strip() or None


def safe_date(val: Any) -> date | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    text = str(val).strip()
    if not text:
        return None
    formats = [
        "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d",
        "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z",
        "%d-%m-%Y", "%m/%d/%Y",
    ]
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.date()
        except ValueError:
            continue
    return None


def _pick_first(*vals: Any) -> Any:
    for v in vals:
        if v is not None:
            return v
    return None


def _extract_asset_paths(record: dict[str, Any]) -> tuple[str | None, str | None, list[str] | None]:
    assets = record.get("assets", {}) or {}
    patent_data = record.get("patent", {}) or {}

    pdf = _pick_first(
        safe_str(assets.get("pdf")),
        safe_str(patent_data.get("pdf")),
    )
    thumbnail = _pick_first(
        safe_str(assets.get("thumbnail")),
        safe_str(patent_data.get("thumbnail")),
    )

    figures = _pick_first(
        safe_str(assets.get("figures")),
        safe_str(patent_data.get("figures")),
    )

    return pdf, thumbnail, figures if figures else None


def normalize_generic(record: dict[str, Any]) -> dict[str, Any]:
    from app.etl.base import extract_meta

    meta = extract_meta(record)
    return {
        "data_source": meta.get("data_source", ""),
        "data_type": meta.get("data_type", "unknown"),
        "record_id": meta.get("record_id", ""),
        "title": safe_str(record.get("title")),
        "description": safe_str(record.get("description") or record.get("abstract") or record.get("summary")),
        "extra_data": json.dumps(record),
        "quality_score": 1.0,
        "quality_flags": "[]",
    }