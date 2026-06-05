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
from app.etl.normalizers.base import _extract_asset_paths, _pick_first, safe_date, safe_str


def _normalize_patent_common(record: dict[str, Any], source: str) -> dict[str, Any]:
    patent = record.get("patent", {}) or {}

    quality_flags: list[str] = []

    title = _pick_first(
        safe_str(patent.get("title")),
        safe_str(patent.get("invention_title")),
        safe_str(record.get("title")),
        safe_str(record.get("invention_title")),
    )
    publication_number = _pick_first(
        safe_str(patent.get("publication_number")),
        safe_str(patent.get("patent_number")),
        safe_str(patent.get("patent_id")),
        safe_str(record.get("publication_number")),
        safe_str(record.get("patent_number")),
        safe_str(record.get("patent_id")),
    )
    application_number = safe_str(
        patent.get("application_number") or patent.get("app_number")
        or record.get("application_number") or record.get("app_number")
    )
    assignee = _pick_first(
        safe_str(patent.get("assignee")),
        safe_str(patent.get("assignee_name")),
        safe_str(patent.get("current_assignee")),
        safe_str(record.get("assignee")),
        safe_str(record.get("assignee_name")),
        safe_str(record.get("current_assignee")),
    )
    inventor = _pick_first(
        safe_str(patent.get("inventor")),
        safe_str(patent.get("inventor_name")),
        safe_str(record.get("inventor")),
        safe_str(record.get("inventor_name")),
    )

    publication_date = safe_date(
        patent.get("publication_date") or patent.get("pub_date")
        or record.get("publication_date") or record.get("pub_date")
    )
    filing_date = safe_date(
        patent.get("filing_date") or patent.get("application_date")
        or record.get("filing_date") or record.get("application_date")
    )
    priority_date = safe_date(
        patent.get("priority_date") or patent.get("earliest_priority_date")
        or record.get("priority_date") or record.get("earliest_priority_date")
    )
    grant_date = safe_date(
        patent.get("grant_date") or patent.get("granted_date")
        or record.get("grant_date") or record.get("granted_date")
    )

    abstract = _pick_first(
        safe_str(patent.get("abstract")),
        safe_str(patent.get("description")),
        safe_str(patent.get("snippet")),
        safe_str(record.get("abstract")),
        safe_str(record.get("description")),
        safe_str(record.get("snippet")),
    )
    legal_status = safe_str(
        patent.get("legal_status") or patent.get("status")
        or record.get("legal_status") or record.get("status")
    )
    ipc = safe_str(
        patent.get("ipc") or patent.get("ipc_classification")
        or record.get("ipc") or record.get("ipc_classification")
    )
    cpc = safe_str(
        patent.get("cpc") or patent.get("cpc_classification")
        or record.get("cpc") or record.get("cpc_classification")
    )
    patent_type = safe_str(
        patent.get("patent_type") or patent.get("type") or patent.get("kind")
        or record.get("patent_type") or record.get("type") or record.get("kind")
    )

    claims_raw = patent.get("claims") or record.get("claims")
    if isinstance(claims_raw, str):
        try:
            claims_raw = json.loads(claims_raw)
        except (json.JSONDecodeError, TypeError):
            claims_raw = {"text": claims_raw}

    original_file, thumbnail, figures = _extract_asset_paths(record)

    if not publication_number:
        quality_flags.append("missing_publication_number")
    if not title:
        quality_flags.append("missing_title")
    if not assignee:
        quality_flags.append("missing_assignee")
    if not abstract:
        quality_flags.append("missing_abstract")

    quality_score = max(0.0, 1.0 - len(quality_flags) * 0.2)

    meta = record.get("_meta", {}) or {}
    record_id = (
        meta.get("record_id")
        or record.get("record_id")
        or publication_number
        or ""
    )

    excluded_keys = {
        "id", "rank", "_meta", "assets", "patent", "entity_matches",
        "measure_matches", "is_similar_document", "_kafka_meta",
        "data_source", "data_type", "record_id",
        "title", "publication_number", "patent_id",
        "application_number", "app_number",
        "assignee", "assignee_name", "current_assignee",
        "inventor", "inventor_name",
        "publication_date", "pub_date", "filing_date", "priority_date",
        "earliest_priority_date", "grant_date", "granted_date",
        "abstract", "description", "legal_status", "status",
        "ipc", "ipc_classification", "cpc", "cpc_classification",
        "patent_type", "type", "kind",
        "claims", "pdf", "thumbnail", "figures", "snippet", "language",
        "family_metadata",
    }

    patent_extra = {k: v for k, v in patent.items() if k not in excluded_keys}
    top_extra = {k: v for k, v in record.items() if k not in excluded_keys}
    extra = {**top_extra, "patent": patent_extra} if patent_extra else top_extra

    return {
        "data_source": source,
        "data_type": record.get("data_type") or patent.get("data_type") or "patent",
        "record_id": record_id,
        "title": title,
        "publication_number": publication_number,
        "application_number": application_number,
        "assignee": assignee,
        "inventor": inventor,
        "publication_date": publication_date,
        "filing_date": filing_date,
        "priority_date": priority_date,
        "grant_date": grant_date,
        "abstract": abstract,
        "claims": json.dumps(claims_raw) if claims_raw else None,
        "legal_status": legal_status,
        "ipc_classification": ipc,
        "cpc_classification": cpc,
        "patent_type": patent_type,
        "original_file": original_file,
        "thumbnail": thumbnail,
        "figures": json.dumps(figures) if figures else None,
        "quality_score": quality_score,
        "quality_flags": json.dumps(quality_flags),
        "extra_data": json.dumps(extra) if extra else None,
    }


def normalize_google_patent(record: dict[str, Any]) -> dict[str, Any]:
    return _normalize_patent_common(record, "google_patent")


register_normalizer("patent", "google_patent", normalize_google_patent)