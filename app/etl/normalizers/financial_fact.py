"""Company financial-fact ODS normalizer."""

from __future__ import annotations

import json
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import safe_str


def normalize_sec_edgar_financial_fact(record: dict[str, Any]) -> dict[str, Any]:
    meta = record.get("_meta", {}) or {}
    return {
        "record_id": safe_str(record.get("record_id")),
        "data_source": safe_str(meta.get("data_source")),
        "data_type": "financial_fact",
        "cik": safe_str(record.get("cik")),
        "entity_name": safe_str(record.get("entity_name")),
        "form": safe_str(record.get("form")),
        "taxonomy": safe_str(record.get("taxonomy")),
        "concept": safe_str(record.get("concept")),
        "concept_label": safe_str(record.get("concept_label")),
        "unit": safe_str(record.get("unit")),
        "value": record.get("value", record.get("val")),
        "accn": safe_str(record.get("accn") or record.get("accession_number")),
        "filed": safe_str(record.get("filed")),
        "start_date": safe_str(record.get("start_date") or record.get("start")),
        "end_date": safe_str(record.get("end_date") or record.get("end")),
        "fy": record.get("fy"),
        "fp": safe_str(record.get("fp")),
        "frame": safe_str(record.get("frame")),
        "is_amendment": record.get("is_amendment"),
    }


def normalize_sec_edgar_financial_facts(
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
                    rows.append(normalize_sec_edgar_financial_fact({
                        "_meta": {
                            "record_id": f"{company['record_id']}:fact:{taxonomy}:{concept}:{unit}:{index}",
                            "data_source": company["data_source"],
                        },
                        "cik": company.get("cik"),
                        "entity_name": company.get("entity_name"),
                        "form": value.get("form"),
                        "accn": value.get("accn"),
                        "filed": value.get("filed"),
                        "end_date": value.get("end"),
                        "start_date": value.get("start"),
                        "concept_label": spec.get("concept_label") or None,
                        "taxonomy": taxonomy,
                        "concept": concept,
                        "unit": unit,
                        **value,
                    }))
    return rows


register_normalizer("financial_fact", "sec_edgar_company", normalize_sec_edgar_financial_fact)
