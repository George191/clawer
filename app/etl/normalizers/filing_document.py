"""Filing-document ODS normalizer."""

from __future__ import annotations

from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_str


def _meta(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("_meta")
    return value if isinstance(value, dict) else {}


def normalize_sec_edgar_filing_document(record: dict[str, Any]) -> dict[str, Any]:
    meta = _meta(record)
    record_id = safe_str(meta.get("record_id"))
    return {
        "record_id": record_id,
        "data_source": safe_str(meta.get("template")),
        "data_type": "filing_document",
        "cik": safe_str(record.get("cik")),
        "accession_number": safe_str(record.get("accession_number")),
        "sequence": safe_str(record.get("sequence")),
        "description": safe_str(record.get("description")),
        "filename": safe_str(record.get("filename")),
        "size": safe_str(record.get("size")),
        "url": safe_str(record.get("url")),
        "doc_type": safe_str(record.get("doc_type")),
    }


def normalize_sec_edgar_filing_documents(
    record: dict[str, Any], field: str
) -> list[dict[str, Any]]:
    files = record.get(field)
    if not isinstance(files, list):
        return []
    meta = _meta(record)
    filing_id = safe_str(meta.get("record_id"))
    source = safe_str(record.get("data_source"))
    sequence = safe_str(record.get("sequence"))
    rows: list[dict[str, Any]] = []
    for _, item in enumerate(files):
        if not isinstance(item, dict):
            continue
        filename = safe_str(item.get("filename"))
        rows.append(normalize_sec_edgar_filing_document({
            "_meta": {
                "record_id": f"{filing_id}:document:{field}:{sequence}",
                "data_source": source,
            },
            "cik": record.get("cik"),
            "accession_number": record.get("accession_number"),
            "sequence": item.get("sequence"),
            "description": item.get("description"),
            "filename": filename,
            "size": item.get("size"),
            "url": item.get("url"),
            "doc_type": item.get("doc_type"),
        }))
    return rows


register_normalizer("filing_document", "sec_edgar_filing", normalize_sec_edgar_filing_document)
