"""Company ODS normalizer."""

from __future__ import annotations

import json
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_date, safe_str


def _normalize_company(record: dict[str, Any]) -> dict[str, Any]:
    meta = record.get("_meta", {}) or {}
    company = record.get("company") or {}
    if not isinstance(company, dict):
        company = {}

    def pick(name: str) -> Any:
        return record.get(name, company.get(name))

    def dump(value: Any) -> str | None:
        return json.dumps(value, ensure_ascii=False) if value is not None else None

    return {
        "record_id": safe_str(meta.get("record_id") or record.get("record_id")) or "",
        "data_source": safe_str(meta.get("data_source") or meta.get("template")) or "company",
        "data_type": "company",
        "cik": safe_str(pick("cik")),
        "ticker": safe_str(record.get("ticker")),
        "name": safe_str(record.get("name") or pick("name")),
        "entity_name": safe_str(pick("entity_name")),
        "entity_type": safe_str(pick("entity_type")),
        "sic": safe_str(pick("sic")),
        "sic_description": safe_str(pick("sic_description")),
        "fiscal_year_end": safe_str(pick("fiscal_year_end")),
        "state_of_incorporation": safe_str(pick("state_of_incorporation")),
        "state_of_location": safe_str(pick("state_of_location")),
        "addresses": dump(pick("addresses")),
        "former_names": dump(pick("former_names")),
        "filing_date": safe_date(record.get("filing_date")),
    }


def normalize_company(record: dict[str, Any]) -> list[dict[str, Any]]:
    from app.etl.normalizers.financial_fact import normalize_financial_facts

    company = _normalize_company(record)
    return [company, *normalize_financial_facts(record, company)]


register_normalizer("company", "sec_edgar_company", normalize_company)
