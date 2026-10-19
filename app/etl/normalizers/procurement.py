"""Contract-award and tender ODS rows for procurement collectors."""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.etl.normalizers import register_normalizer
from app.etl.normalizers.base import json_dumps, safe_date, safe_datetime, safe_str
from app.utils.record_id import resolve_record_id


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "").removeprefix("$").strip()
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return {"true": True, "yes": True, "1": True,
            "false": False, "no": False, "0": False}.get(str(value).strip().lower())


def _source_datetime(value: Any) -> datetime | None:
    # SAM's public CSV does not define the timezone of its naive timestamps.
    # Retain those values in *_raw rather than silently interpreting them as UTC.
    if isinstance(value, datetime):
        return value if value.tzinfo is not None and value.utcoffset() is not None else None
    text = safe_str(value)
    if not text or not re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", text):
        return None
    return safe_datetime(text)


def _business_date(value: Any) -> date | None:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).date()
        except ValueError:
            pass
    return safe_date(value)


def _award_source_id(record: dict[str, Any]) -> str | None:
    source = record.get("source_data") or {}
    contract = source.get("contractId") or {}
    values = [
        (contract.get("subtier") or {}).get("code", record.get("agency_code")),
        contract.get("piid", record.get("contract_number")),
        contract.get("modificationNumber", record.get("modification_number")),
        contract.get("transactionNumber", record.get("transaction_number")) or "0",
        (contract.get("referencedIDVSubtier") or {}).get("code", record.get("referenced_idv_agency_code")),
        contract.get("referencedIDVPiid", record.get("referenced_idv_piid")),
        contract.get("referencedIDVModificationNumber", record.get("referenced_idv_modification_number")),
    ]
    identifiers = [str(value if value is not None else "").strip() for value in values]
    if all(identifiers[:3]):
        return "sam_gov_awards:" + json.dumps(identifiers, ensure_ascii=False, separators=(",", ":"))
    return safe_str(record.get("id"))


def _common(record: dict[str, Any], source: str, data_type: str, source_id: str | None) -> dict[str, Any]:
    meta = record.get("_meta") or {}
    record_id = safe_str(meta.get("record_id"))
    if not record_id:
        if not source_id:
            raise ValueError(f"{source} record has no stable identity")
        record_id = resolve_record_id({"id": source_id})
    return {
        "record_id": record_id,
        "data_source": safe_str(meta.get("data_source")) or source,
        "data_type": data_type,
        "title": safe_str(record.get("title")),
        "content": safe_str(record.get("content")),
        "url": safe_str(record.get("url")),
        "source_updated_at": _source_datetime(record.get("updated_at")),
        "source_updated_at_raw": safe_str(record.get("updated_at")),
        "captured_at": safe_datetime(meta.get("updated_at")),
        "deleted": _boolean(record.get("deleted")) is True,
    }


def _normalize_contract(
    record: dict[str, Any], source: str, source_id: str | None,
) -> dict[str, Any]:
    row = _common(record, source, "contract", source_id)
    for field in (
        "contract_number", "modification_number", "transaction_number",
        "referenced_idv_agency_code", "referenced_idv_piid", "referenced_idv_modification_number",
        "award_or_idv", "contract_type", "contract_type_code", "fiscal_year",
        "agency_code", "agency_name", "contracting_department_code", "contracting_department_name",
        "contracting_office_code", "contracting_office_name", "funding_agency_code", "funding_agency_name",
        "funding_department_code", "funding_department_name", "funding_office_code", "funding_office_name",
        "awardee_name", "awardee_uei", "awardee_cage_code", "naics_code", "psc_code", "amount_type",
    ):
        row[field] = safe_str(record.get(field))
    for field in (
        "solicitation_date", "award_date", "period_of_performance_start_date", "current_completion_date",
        "ultimate_completion_date", "last_date_to_order",
    ):
        row[field] = _business_date(record.get(field))
    for field in (
        "amount", "action_obligation", "base_dollars_obligated", "base_and_exercised_options_value",
        "base_and_all_options_value", "total_action_obligation", "total_base_and_exercised_options_value",
        "total_base_and_all_options_value",
    ):
        row[field] = _decimal(record.get(field))
    row.update({
        "solicitation_number": safe_str(record.get("solicitation_id")),
        "source_created_at": _source_datetime(record.get("created_at")),
        "source_created_at_raw": safe_str(record.get("created_at")),
        "awardee_address": json_dumps(record.get("awardee_address")),
        "place_of_performance": json_dumps(record.get("place_of_performance")),
        "currency": safe_str(record.get("currency")) or "USD",
    })
    return row


def normalize_sam_gov_award(record: dict[str, Any]) -> dict[str, Any]:
    return _normalize_contract(record, "sam_gov_awards", _award_source_id(record))


def normalize_usaspending_contract(record: dict[str, Any]) -> dict[str, Any]:
    return _normalize_contract(record, "usaspending", safe_str(record.get("id")))


def normalize_sam_gov_opportunity(record: dict[str, Any]) -> dict[str, Any]:
    source = record.get("source_data") or {}
    notice_id = safe_str(record.get("id")) or safe_str(source.get("NoticeId")) or safe_str(source.get("noticeId"))
    row = _common(record, "sam_gov_opportunities", "tender", notice_id)
    for field in (
        "solicitation_number", "notice_type", "base_type", "agency_code", "agency_name", "naics_code",
        "psc_code", "archive_type", "set_aside_code", "set_aside", "organization_type",
        "description_url", "additional_info_url",
    ):
        row[field] = safe_str(record.get(field))
    for field in ("contacts", "place_of_performance", "office_address", "attachments", "award"):
        row[field] = json_dumps(record.get(field))
    award = record.get("award") or {}
    awardee = award.get("awardee") or {}
    award_amount = _decimal(award.get("amount"))
    if award_amount is None:
        award_amount = _decimal(award.get("amount_raw"))
    row.update({
        "notice_id": notice_id,
        "source_published_at": _source_datetime(record.get("publish_time")),
        "source_published_at_raw": safe_str(record.get("publish_time")),
        "response_deadline": _source_datetime(record.get("response_deadline")),
        "response_deadline_raw": safe_str(record.get("response_deadline")),
        "archive_date": _business_date(record.get("archive_date")),
        "active": _boolean(record.get("active")),
        "attachments_available": _boolean(record.get("attachments_available")),
        "award_number": safe_str(award.get("number")),
        "award_date": _business_date(award.get("date")),
        "award_amount": award_amount,
        "awardee_name": safe_str(awardee.get("name")),
        "currency": safe_str(record.get("currency")) or "USD",
    })
    return row


register_normalizer("contract", "sam_gov_awards", normalize_sam_gov_award)
register_normalizer("contract", "sam_gov_opportunities", normalize_sam_gov_opportunity)
register_normalizer("contract", "usaspending", normalize_usaspending_contract)
