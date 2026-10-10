"""Split SEC companyfacts into independently stored financial observations."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Iterator

from app.utils.record_id import resolve_record_id

FACT_COLLECTION = "sec_edgar_financial_fact"
FACT_DEDUP_FIELDS = (
    "cik", "accn", "taxonomy", "concept", "unit", "filed", "start_date",
    "end_date", "form", "fy", "fp", "frame",
)


def _identity_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is not None:
        for fmt in (
            "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z", "%d-%m-%Y", "%m/%d/%Y",
        ):
            try:
                return datetime.strptime(str(value).strip(), fmt).date().isoformat()
            except ValueError:
                continue
    return None


def financial_fact_identity(record: dict[str, Any]) -> dict[str, Any]:
    """Match the existing ODS financial-fact identity, including absent fields."""
    identity = {}
    for field in FACT_DEDUP_FIELDS:
        value = record.get(field)
        if field in {"filed", "start_date", "end_date"}:
            value = _identity_date(value)
        elif field != "fy":
            value = (str(value).strip() or None) if value is not None else None
        identity[field] = value
    return identity


def iter_sec_financial_facts(record: dict[str, Any]) -> Iterator[dict[str, Any]]:
    facts = record.get("facts")
    if facts is None:
        return
    if not isinstance(facts, dict):
        raise ValueError("SEC facts must be an object")
    meta = record.get("_meta") or {}
    cik = record.get("cik")
    company_id = meta.get("record_id") or resolve_record_id({"cik": cik})
    search_params = record.get("_meta_search_params") or meta.get("search_params") or {}
    for taxonomy, concepts in facts.items():
        if not isinstance(concepts, dict):
            raise ValueError(f"Invalid SEC taxonomy: {taxonomy}")
        for concept, spec in concepts.items():
            if not isinstance(spec, dict) or not isinstance(spec.get("units"), dict):
                raise ValueError(f"Invalid SEC concept: {taxonomy}.{concept}")
            for unit, observations in spec["units"].items():
                if not isinstance(observations, list):
                    raise ValueError(f"Invalid SEC observations: {taxonomy}.{concept}.{unit}")
                for observation in observations:
                    if not isinstance(observation, dict) or observation.get("val") is None:
                        raise ValueError(f"Invalid SEC observation: {taxonomy}.{concept}.{unit}")
                    yield {
                        **observation,
                        "data_type": "financial_fact",
                        "company_record_id": company_id,
                        "cik": cik,
                        "entity_name": record.get("name") or record.get("entity_name"),
                        "taxonomy": taxonomy,
                        "concept": concept,
                        "concept_label": spec.get("label"),
                        "concept_metadata": {key: value for key, value in spec.items() if key != "units"},
                        "unit": unit,
                        "value": observation["val"],
                        "start_date": observation.get("start"),
                        "end_date": observation.get("end"),
                        "_meta_search_params": search_params,
                    }
