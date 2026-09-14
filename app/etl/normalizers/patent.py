"""ODS 专利标准化器。

按数据源拆分为独立注册函数，便于扩展新数据源时只需新增文件 + 注册。

接收的 record 为 raw_data 原始采集数据，结构因源而异。
google_patent 的专利字段嵌套在 `record.patent` 子对象下。

注册方式：
    from app.etl.normalizers import register_normalizer
    register_normalizer("patent", "new_source", normalize_new_source_patent)
"""

from __future__ import annotations

import json
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import apply_asset_path_overrides, safe_date, safe_str


def _meta(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("_meta")
    return value if isinstance(value, dict) else {}


def _patent(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("patent")
    return value if isinstance(value, dict) else {}


def normalize_google_patent(record: dict[str, Any]) -> dict[str, Any]:

    def pick(name: str) -> Any:
        return patent.get(name)

    def dump(value: Any) -> str | None:
        return json.dumps(value, ensure_ascii=False) if value is not None else None
    
    patent = _patent(record)
    meta = _meta(record)
    record, _ = apply_asset_path_overrides(record)

    figures = patent.get("figures")

    return {
        "data_source": safe_str(meta.get("template")),
        "data_type": safe_str(meta.get("data_type")),
        "record_id": safe_str(meta.get("record_id")),
        "title": safe_str(pick("title")),
        "publication_number": safe_str(pick("publication_number")),
        "application_number": None,
        "assignee": safe_str(pick("assignee")),
        "inventor": safe_str(pick("inventor")),
        "publication_date": safe_date(pick("publication_date")),
        "filing_date": safe_date(pick("filing_date")),
        "priority_date": safe_date(pick("priority_date")),
        "grant_date": safe_date(pick("grant_date")),
        "abstract": safe_str(pick("snippet")),
        "claims": None,
        "legal_status": None,
        "ipc_classification": None,
        "cpc_classification": None,
        "patent_type": None,
        "url": safe_str(pick("pdf")),
        "thumbnail": safe_str(pick("thumbnail")),
        "figures": dump(figures),
    }


register_normalizer("patent", "google_patent", normalize_google_patent)
