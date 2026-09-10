"""Filing-document ODS normalizer."""

from __future__ import annotations

from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_str


def normalize_filing_document(record: dict[str, Any]) -> dict[str, Any]:
    meta = record.get("_meta", {}) or {}
    return {
        "record_id": safe_str(meta.get("record_id") or record.get("record_id")) or "",
        "data_source": safe_str(meta.get("data_source") or meta.get("template")) or "filing_document",
        "data_type": "filing_document",
        "filing_record_id": safe_str(record.get("filing_record_id")),
        "cik": safe_str(record.get("cik")),
        "accession_number": safe_str(record.get("accession_number")),
        "file_category": safe_str(record.get("file_category")),
        "sequence": safe_str(record.get("sequence")),
        "description": safe_str(record.get("description")),
        "document": safe_str(record.get("document") or record.get("filename")),
        "doc_type": safe_str(record.get("doc_type") or record.get("type")),
        "size": safe_str(record.get("size")),
        "url": safe_str(record.get("url")),
    }


def normalize_filing_documents(
    record: dict[str, Any], field: str
) -> list[dict[str, Any]]:
    files = record.get(field) or []
    if not isinstance(files, list):
        return []
    meta = record.get("_meta", {}) or {}
    source = safe_str(meta.get("data_source") or meta.get("template")) or "filing_document"
    filing_id = safe_str(meta.get("record_id") or record.get("record_id")) or ""
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            continue
        document = safe_str(item.get("document") or item.get("filename"))
        identity = document or safe_str(item.get("url")) or str(index)
        rows.append(normalize_filing_document({
            "_meta": {"record_id": f"{filing_id}:document:{field}:{identity}", "data_source": source},
            "filing_record_id": filing_id,
            "cik": record.get("cik"),
            "accession_number": record.get("accession_number"),
            "file_category": "document_format" if field == "document_format_files" else "data",
            **item,
        }))
    return rows


register_normalizer("filing_document", "sec_edgar_filing", normalize_filing_document)
