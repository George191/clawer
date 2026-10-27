"""SEC financial-fact ODS normalizer."""

from __future__ import annotations

import json
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_date, safe_str


def _meta(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("_meta")
    return value if isinstance(value, dict) else {}


def normalize_sec_edgar_financial_fact(record: dict[str, Any]) -> dict[str, Any]:

    def pick(name: str) -> Any:
        return record.get(name)

    def dump(value: Any) -> str | None:
        return json.dumps(value, ensure_ascii=False) if value is not None else None

    meta = _meta(record)
    return {
        "record_id": safe_str(meta.get("record_id")),
        "data_source": safe_str(record.get("data_source")),
        "data_type": "financial_fact",
        "cik": safe_str(pick("cik")),
        "entity_name": safe_str(pick("entity_name")),
        "form": safe_str(pick("form")),
        "taxonomy": safe_str(pick("taxonomy")),
        "concept": safe_str(pick("concept")),
        "concept_label": safe_str(pick("concept_label")),
        "unit": safe_str(pick("unit")),
        "value": pick("value"),
        "accn": safe_str(pick("accn")),
        "filed": safe_date(pick("filed")),
        "start_date": safe_date(pick("start_date")),
        "end_date": safe_date(pick("end_date")),
        "fy": pick("fy"),
        "fp": safe_str(pick("fp")),
        "frame": safe_str(pick("frame")),
        "is_amendment": pick("is_amendment"),
    }


register_normalizer("financial_fact", "*", normalize_sec_edgar_financial_fact)
