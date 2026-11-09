"""Normalize search page/results without changing source identity or snapshots."""

from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import json_dumps, safe_datetime, safe_str

ODS_FIELDS = {
    "search_result": {
        "kind": "TEXT",
        "source_engine": "TEXT",
        "query": "TEXT",
        "language": "TEXT",
        "page_number": "INTEGER",
        "position": "INTEGER",
        "url": "TEXT",
        "title": "TEXT",
        "summary": "TEXT",
        "site_name": "TEXT",
        "displayed_url": "TEXT",
        "card_text": "TEXT",
        "card_html": "TEXT",
        "card_truncated": "BOOLEAN",
        "search_url": "TEXT",
        "list_html_path": "TEXT",
        "list_http_html_path": "TEXT",
        "list_html_paths": "JSONB",
        "list_http_status": "INTEGER",
        "list_content_type": "TEXT",
        "list_sha256": "TEXT",
        "list_byte_length": "BIGINT",
        "captured_at": "TIMESTAMPTZ",
        "page_text": "TEXT",
        "page_text_truncated": "BOOLEAN",
        "result_count": "INTEGER",
        "has_next_page": "BOOLEAN",
        "detail_status": "TEXT",
        "detail_html_path": "TEXT",
        "detail_url": "TEXT",
        "detail_title": "TEXT",
        "detail_http_status": "INTEGER",
        "detail_content_type": "TEXT",
        "detail_error": "TEXT",
        "detail_sha256": "TEXT",
        "detail_byte_length": "BIGINT",
    },
}


def _search_integer(value: Any, bits: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return number if -(2 ** (bits - 1)) <= number < 2 ** (bits - 1) else None


def _search_boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    return None


def normalize_search_result(record: dict[str, Any]) -> dict[str, Any]:
    meta = record.get("_meta") or {}
    normalized = {
        "record_id": safe_str(meta.get("record_id")),
        "data_source": safe_str(record.get("data_source")),
        "data_type": "search_result",
    }
    for name, sql_type in ODS_FIELDS["search_result"].items():
        value = record.get(name)
        if sql_type == "INTEGER":
            normalized[name] = _search_integer(value, 32)
        elif sql_type == "BIGINT":
            normalized[name] = _search_integer(value, 64)
        elif sql_type == "BOOLEAN":
            normalized[name] = _search_boolean(value)
        elif sql_type == "TIMESTAMPTZ":
            normalized[name] = safe_datetime(value)
        elif sql_type == "JSONB":
            normalized[name] = json_dumps(value)
        else:
            normalized[name] = safe_str(value)
    expected_engine = {
        "google_search": "google",
        "search_result_google": "google",
        "duckduckgo_search": "duckduckgo",
        "search_result_duckduckgo": "duckduckgo",
        "bing_search": "bing",
        "search_result_bing": "bing",
        "brave_search": "brave",
        "search_result_brave": "brave",
    }.get(normalized["data_source"])
    if expected_engine and normalized["source_engine"] not in (None, expected_engine):
        raise ValueError("Search engine does not match its business source")
    normalized["source_engine"] = normalized["source_engine"] or expected_engine
    return normalized


NORMALIZERS = {"search_result": normalize_search_result}

register_normalizer("search_result", "*", normalize_search_result)
