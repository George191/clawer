"""Company financial-fact ODS normalizer."""

from __future__ import annotations

from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_str


def normalize_financial_fact(record: dict[str, Any]) -> dict[str, Any]:
    meta = record.get("_meta", {}) or {}
    return {
        "record_id": safe_str(meta.get("record_id") or record.get("record_id")) or "",
        "data_source": safe_str(meta.get("data_source") or meta.get("template")) or "financial_fact",
        "data_type": "financial_fact",
        "company_record_id": safe_str(record.get("company_record_id")),
        "cik": safe_str(record.get("cik")),
        "taxonomy": safe_str(record.get("taxonomy")),
        "concept": safe_str(record.get("concept")),
        "unit": safe_str(record.get("unit")),
        "value": record.get("value", record.get("val")),
        "accession_number": safe_str(record.get("accession_number")),
        "fiscal_year": record.get("fiscal_year", record.get("fy")),
        "fiscal_period": safe_str(record.get("fiscal_period", record.get("fp"))),
        "filed": safe_str(record.get("filed")),
        "frame": safe_str(record.get("frame")),
        "start_date": safe_str(record.get("start_date", record.get("start"))),
        "end_date": safe_str(record.get("end_date", record.get("end"))),
    }


def normalize_financial_facts(
    record: dict[str, Any], company: dict[str, Any]
) -> list[dict[str, Any]]:
    facts = record.get("facts") or {}
    if not isinstance(facts, dict):
        return []
    rows: list[dict[str, Any]] = []
    for taxonomy, concepts in facts.items():
        if not isinstance(concepts, dict):
            continue
        for concept, spec in concepts.items():
            units = spec.get("units", {}) if isinstance(spec, dict) else {}
            for unit, values in units.items():
                for index, value in enumerate(values if isinstance(values, list) else []):
                    if not isinstance(value, dict):
                        continue
                    rows.append(normalize_financial_fact({
                        "_meta": {
                            "record_id": f"{company['record_id']}:fact:{taxonomy}:{concept}:{unit}:{index}",
                            "data_source": company["data_source"],
                        },
                        "company_record_id": company["record_id"],
                        "cik": company.get("cik"),
                        "taxonomy": taxonomy,
                        "concept": concept,
                        "unit": unit,
                        **value,
                    }))
    return rows


register_normalizer("financial_fact", "sec_edgar_company", normalize_financial_fact)
