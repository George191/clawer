"""Company ODS normalizer."""

from __future__ import annotations

import json
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_str
from app.etl.normalizers.financial_fact import normalize_sec_edgar_financial_facts


def _meta(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("_meta")
    return value if isinstance(value, dict) else {}


def _normalize_sec_edgar_company(record: dict[str, Any]) -> dict[str, Any]:
    meta = _meta(record)

    def pick(name: str) -> Any:
        return record.get(name)

    def dump(value: Any) -> str | None:
        return json.dumps(value, ensure_ascii=False) if value is not None else None

    return {
        "record_id": safe_str(meta.get("record_id")),
        "data_source": safe_str(meta.get("template")),
        "data_type": safe_str(pick("data_type")),
        "cik": safe_str(pick("cik")),
        "name": safe_str(pick("name")),
        "entity_type": safe_str(pick("entity_type")),
        "exchanges": dump(pick("exchanges")),
        "tickers": dump(pick("tickers")),
        "sic": safe_str(pick("sic")),
        "sic_description": safe_str(pick("sic_description")),
        "address": dump(pick("address") or pick("addresses")),
        "website": safe_str(pick("website")),
        "ein": safe_str(pick("ein")),
        "description": safe_str(pick("description")),
        "category": safe_str(pick("category")),
        "phone": safe_str(pick("phone")),
        "former_names": dump(pick("former_names")),
        "investor_website": safe_str(pick("investor_website")),
    }


def normalize_sec_edgar_company(record: dict[str, Any]) -> list[dict[str, Any]]:

    company = _normalize_sec_edgar_company(record)
    return [company, *normalize_sec_edgar_financial_facts(record, company)]


register_normalizer("company", "sec_edgar_company", normalize_sec_edgar_company)
