"""Filing (申报) ODS normalizer."""

from __future__ import annotations

import json
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_date, safe_datetime, safe_str


def _int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _normalize_filing(record: dict[str, Any]) -> dict[str, Any]:
    meta = record.get("_meta", {}) or {}
    return {
        "record_id": safe_str(meta.get("record_id") or record.get("record_id")) or "",
        "data_source": safe_str(meta.get("data_source") or meta.get("template")) or "filing",
        "data_type": "filing",
        "cik": safe_str(record.get("cik")),
        "accession_number": safe_str(record.get("accession_number")),
        "form": safe_str(record.get("form")),
        "filing_date": safe_date(record.get("filing_date")),
        "report_date": safe_date(record.get("report_date")),
        "acceptance_datetime": safe_datetime(record.get("acceptance_datetime")),
        "act": safe_str(record.get("act")),
        "file_number": safe_str(record.get("file_number")),
        "film_number": safe_str(record.get("film_number")),
        "items": safe_str(record.get("items")),
        "core_type": safe_str(record.get("core_type")),
        "size": _int(record.get("size")),
        "is_xbrl": record.get("is_xbrl"),
        "is_inline_xbrl": record.get("is_inline_xbrl"),
        "filing_base": safe_str(record.get("filing_base")),
        "filing_index_url": safe_str(record.get("filing_index_url")),
        "filing_index_status": safe_str(record.get("filing_index_status")),
        "submission_documents": json.dumps(record.get("submission_documents"), ensure_ascii=False)
        if record.get("submission_documents") is not None else None,
    }


def normalize_filing(record: dict[str, Any]) -> list[dict[str, Any]]:
    from app.etl.normalizers.filing_document import normalize_filing_documents

    filing = _normalize_filing(record)
    return [
        filing,
        *normalize_filing_documents(record, "document_format_files"),
        *normalize_filing_documents(record, "data_files"),
    ]


register_normalizer("filing", "sec_edgar_filing", normalize_filing)
