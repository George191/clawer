"""Filing (申报) ODS normalizer."""

from __future__ import annotations

import json
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_date, safe_datetime, safe_str


def _meta(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("_meta")
    return value if isinstance(value, dict) else {}


def _safe_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "t", "yes", "y"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", ""}:
        return False
    return None


def _normalize_sec_edgar_filing(record: dict[str, Any]) -> dict[str, Any]:

    def pick(name: str) -> Any:
        return record.get(name)

    def dump(value: Any) -> str | None:
        return json.dumps(value, ensure_ascii=False) if value is not None else None
    
    meta = _meta(record)
    record_id = safe_str(meta.get("record_id"))
    return {
        "record_id": record_id,
        "data_source": safe_str(meta.get("template")),
        "data_type": safe_str(pick("data_type")),
        "cik": safe_str(pick("cik")),
        "accession_number": safe_str(pick("accession_number")),
        "form": safe_str(pick("form")),
        "filing_date": safe_date(pick("filing_date")),
        "report_date": safe_date(pick("report_date")),
        "acceptance_datetime": safe_datetime(pick("acceptance_datetime")),
        "act": safe_str(pick("act")),
        "file_number": safe_str(pick("file_number")),
        "film_number": safe_str(pick("film_number")),
        "items": safe_str(pick("items")),
        "core_type": safe_str(record.v("core_type")),
        "size": pick("size"),
        "is_xbrl": _safe_bool(pick("is_xbrl")),
        "is_inline_xbrl": _safe_bool(pick("is_inline_xbrl")),
        "filing_base": safe_str(pick("filing_base")),
        "filing_index_url": safe_str(pick("filing_index_url")),
    }


def normalize_sec_edgar_filing(record: dict[str, Any]) -> list[dict[str, Any]]:
    from app.etl.normalizers.filing_document import normalize_sec_edgar_filing_documents

    filing = _normalize_sec_edgar_filing(record)
    return [
        filing,
        *normalize_sec_edgar_filing_documents(record, "document_format_files"),
        *normalize_sec_edgar_filing_documents(record, "data_files"),
    ]


register_normalizer("filing", "sec_edgar_filing", normalize_sec_edgar_filing)
