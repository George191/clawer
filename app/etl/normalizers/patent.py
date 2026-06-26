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


def normalize_google_patent(record: dict[str, Any]) -> dict[str, Any]:
    record, _ = apply_asset_path_overrides(record)
    patent = record.get("patent", {}) or {}
    meta = record.get("_meta", {}) or {}

    quality_flags: list[str] = []

    title = safe_str(patent.get("title"))
    publication_number = safe_str(patent.get("publication_number"))
    assignee = safe_str(patent.get("assignee"))
    inventor = safe_str(patent.get("inventor"))

    publication_date = safe_date(patent.get("publication_date"))
    filing_date = safe_date(patent.get("filing_date"))
    priority_date = safe_date(patent.get("priority_date"))
    grant_date = safe_date(patent.get("grant_date"))

    abstract = safe_str(patent.get("snippet"))
    url = safe_str(patent.get("pdf"))
    thumbnail = safe_str(patent.get("thumbnail"))
    figures = patent.get("figures")

    if not publication_number:
        quality_flags.append("missing_publication_number")
    if not title:
        quality_flags.append("missing_title")
    if not assignee:
        quality_flags.append("missing_assignee")
    if not abstract:
        quality_flags.append("missing_abstract")

    quality_score = max(0.0, 1.0 - len(quality_flags) * 0.2)

    record_id = (
        meta.get("record_id")
        or publication_number
        or ""
    )

    return {
        "data_source": meta.get("data_source") or meta.get("template") or "google_patent",
        "data_type": "patent",
        "record_id": record_id,
        "title": title,
        "publication_number": publication_number,
        "application_number": None,
        "assignee": assignee,
        "inventor": inventor,
        "publication_date": publication_date,
        "filing_date": filing_date,
        "priority_date": priority_date,
        "grant_date": grant_date,
        "abstract": abstract,
        "claims": None,
        "legal_status": None,
        "ipc_classification": None,
        "cpc_classification": None,
        "patent_type": None,
        "url": url,
        "thumbnail": thumbnail,
        "figures": json.dumps(figures) if figures else None,
        "quality_score": quality_score,
        "quality_flags": json.dumps(quality_flags),
    }
register_normalizer("patent", "google_patent", normalize_google_patent)
