"""Filing-document ODS normalizer."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import apply_asset_path_overrides, safe_str


def _meta(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("_meta")
    return value if isinstance(value, dict) else {}


def _document_record_id(source_item: dict[str, Any]) -> str:
    identity = {
        field: safe_str(source_item.get(field)) or ""
        for field in (
            "accession_number",
            "url",
            "filename",
            "sequence",
            "description",
            "doc_type",
            "size",
        )
    }
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()


def normalize_sec_edgar_filing_document(record: dict[str, Any]) -> dict[str, Any]:

    def pick(name: str) -> Any:
        return record.get(name)

    def dump(value: Any) -> str | None:
        return json.dumps(value, ensure_ascii=False) if value is not None else None

    meta = _meta(record)
    record_id = safe_str(meta.get("record_id"))
    return {
        "record_id": record_id,
        "data_source": safe_str(meta.get("data_source")),
        "data_type": "filing_document",
        "cik": safe_str(pick("cik")),
        "accession_number": safe_str(pick("accession_number")),
        "sequence": safe_str(pick("sequence")),
        "description": safe_str(pick("description")),
        "filename": safe_str(pick("filename")),
        "size": safe_str(pick("size")),
        "url": safe_str(pick("url")),
        "doc_type": safe_str(pick("doc_type")),
    }


def normalize_sec_edgar_filing_documents(
    record: dict[str, Any], field: str
) -> list[dict[str, Any]]:
    normalized_record, _ = apply_asset_path_overrides(record)
    files = normalized_record.get(field)
    source_files = record.get(field)
    if not isinstance(files, list):
        return []
    meta = _meta(record)
    source = safe_str(meta.get("template"))
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            continue
        source_item = (
            source_files[index]
            if isinstance(source_files, list)
            and index < len(source_files)
            and isinstance(source_files[index], dict)
            else item
        )
        source_url = safe_str(source_item.get("url"))
        if not source_url:
            continue
        filename = safe_str(item.get("filename"))
        identity_source = {
            **source_item,
            "accession_number": record.get("accession_number"),
        }
        document = {
            "_meta": {
                "record_id": _document_record_id(identity_source),
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
        }
        rows.append(normalize_sec_edgar_filing_document(document))
    return rows


register_normalizer("filing_document", "sec_edgar_filing", normalize_sec_edgar_filing_document)
