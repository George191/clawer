"""Organization ODS normalizer."""

from __future__ import annotations

import json
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_str
from app.etl.normalizers.space import normalize_organization


def _meta(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("_meta")
    return value if isinstance(value, dict) else {}


def normalize_sec_edgar_organization(record: dict[str, Any]) -> dict[str, Any]:
    meta = _meta(record)

    def pick(name: str) -> Any:
        return record.get(name)

    def dump(value: Any) -> str | None:
        return json.dumps(value, ensure_ascii=False) if value is not None else None

    organization = normalize_organization({key: value for key, value in record.items() if key != "facts"})
    organization.update({
        "record_id": safe_str(meta.get("record_id")),
        "data_source": safe_str(record.get("data_source")),
        "data_type": "organization",
        "cik": safe_str(pick("cik")),
        "name": safe_str(pick("name")) or "",
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
    })
    return organization


register_normalizer("organization", "organization_sec_edgar", normalize_sec_edgar_organization)
