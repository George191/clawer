"""Column definitions shared by current-table DDL and upserts."""

from __future__ import annotations

import re
from typing import TypedDict


class _TableDefinition(TypedDict):
    columns: dict[str, str]
    primary_key: tuple[str, ...]
    partition_columns: tuple[str, ...]


class TableDefinition(_TableDefinition, total=False):
    checks: tuple[str, ...]
    indexes: tuple[tuple[str, str, str | None, str], ...]


RECORD_IDENTITY_COLUMNS = ("record_id", "data_source", "data_type")
_AUDIT_COLUMNS = {
    "created_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
    "updated_at": "TIMESTAMPTZ NOT NULL DEFAULT NOW()",
}

_SPACE_COMMON_COLUMNS = {
    "source_id": "TEXT",
    "url": "TEXT",
    "description": "TEXT",
    "captured_at": "TIMESTAMPTZ",
    "attributes": "JSONB",
}

_CATALOG_FIELDS: dict[str, dict[str, str]] = {
    "contract": {
        "title": "TEXT",
        "content": "TEXT",
        "url": "TEXT",
        "source_created_at": "TIMESTAMPTZ",
        "source_created_at_raw": "TEXT",
        "source_updated_at": "TIMESTAMPTZ",
        "source_updated_at_raw": "TEXT",
        "captured_at": "TIMESTAMPTZ",
        "contract_number": "TEXT",
        "modification_number": "TEXT",
        "transaction_number": "TEXT",
        "referenced_idv_agency_code": "TEXT",
        "referenced_idv_piid": "TEXT",
        "referenced_idv_modification_number": "TEXT",
        "award_or_idv": "TEXT",
        "contract_type": "TEXT",
        "contract_type_code": "TEXT",
        "solicitation_number": "TEXT",
        "solicitation_date": "DATE",
        "award_date": "DATE",
        "period_of_performance_start_date": "DATE",
        "current_completion_date": "DATE",
        "ultimate_completion_date": "DATE",
        "last_date_to_order": "DATE",
        "fiscal_year": "TEXT",
        "agency_code": "TEXT",
        "agency_name": "TEXT",
        "contracting_department_code": "TEXT",
        "contracting_department_name": "TEXT",
        "contracting_office_code": "TEXT",
        "contracting_office_name": "TEXT",
        "funding_agency_code": "TEXT",
        "funding_agency_name": "TEXT",
        "funding_department_code": "TEXT",
        "funding_department_name": "TEXT",
        "funding_office_code": "TEXT",
        "funding_office_name": "TEXT",
        "awardee_name": "TEXT",
        "awardee_uei": "TEXT",
        "awardee_cage_code": "TEXT",
        "awardee_address": "JSONB",
        "place_of_performance": "JSONB",
        "naics_code": "TEXT",
        "psc_code": "TEXT",
        "amount": "NUMERIC",
        "amount_type": "TEXT",
        "action_obligation": "NUMERIC",
        "base_dollars_obligated": "NUMERIC",
        "base_and_exercised_options_value": "NUMERIC",
        "base_and_all_options_value": "NUMERIC",
        "total_action_obligation": "NUMERIC",
        "total_base_and_exercised_options_value": "NUMERIC",
        "total_base_and_all_options_value": "NUMERIC",
        "currency": "TEXT",
        "deleted": "BOOLEAN NOT NULL DEFAULT FALSE",
    },
    "tender": {
        "title": "TEXT",
        "content": "TEXT",
        "url": "TEXT",
        "source_published_at": "TIMESTAMPTZ",
        "source_published_at_raw": "TEXT",
        "source_updated_at": "TIMESTAMPTZ",
        "source_updated_at_raw": "TEXT",
        "captured_at": "TIMESTAMPTZ",
        "notice_id": "TEXT",
        "solicitation_number": "TEXT",
        "notice_type": "TEXT",
        "base_type": "TEXT",
        "agency_code": "TEXT",
        "agency_name": "TEXT",
        "naics_code": "TEXT",
        "psc_code": "TEXT",
        "response_deadline": "TIMESTAMPTZ",
        "response_deadline_raw": "TEXT",
        "active": "BOOLEAN",
        "archive_type": "TEXT",
        "archive_date": "DATE",
        "set_aside_code": "TEXT",
        "set_aside": "TEXT",
        "organization_type": "TEXT",
        "description_url": "TEXT",
        "additional_info_url": "TEXT",
        "contacts": "JSONB",
        "place_of_performance": "JSONB",
        "office_address": "JSONB",
        "attachments": "JSONB",
        "attachments_available": "BOOLEAN",
        "award": "JSONB",
        "award_number": "TEXT",
        "award_date": "DATE",
        "award_amount": "NUMERIC",
        "awardee_name": "TEXT",
        "currency": "TEXT",
        "deleted": "BOOLEAN NOT NULL DEFAULT FALSE",
    },
    "financial_fact": {
        "cik": "TEXT NOT NULL",
        "entity_name": "TEXT",
        "taxonomy": "TEXT NOT NULL",
        "concept": "TEXT NOT NULL",
        "concept_label": "TEXT",
        "unit": "TEXT NOT NULL",
        "value": "NUMERIC NOT NULL",
        "accn": "TEXT NOT NULL",
        "form": "TEXT NOT NULL",
        "filed": "DATE NOT NULL",
        "fy": "INTEGER",
        "fp": "TEXT",
        "frame": "TEXT",
        "start_date": "DATE",
        "end_date": "DATE",
        "is_amendment": "BOOLEAN NOT NULL DEFAULT FALSE",
    },
    "social_account": {
        "account_id": "BIGSERIAL NOT NULL",
        "platform_account_id": "TEXT NOT NULL",
        "username": "TEXT",
        "display_name": "TEXT",
        "profile_url": "TEXT",
        "avatar_url": "TEXT",
        "bio": "TEXT",
        "account_type": "TEXT",
        "is_verified": "BOOLEAN",
        "is_private": "BOOLEAN",
        "account_created_at": "TIMESTAMPTZ",
        "follower_count": "BIGINT",
        "following_count": "BIGINT",
        "content_count": "BIGINT",
        "location": "TEXT",
        "language": "TEXT",
        "extra": "JSONB NOT NULL DEFAULT '{}'::JSONB",
        "captured_at": "TIMESTAMPTZ",
    },
    "social_content": {
        "content_id": "BIGSERIAL NOT NULL",
        "platform_content_id": "TEXT NOT NULL",
        "content_type": "TEXT NOT NULL",
        "author_account_id": "BIGINT",
        "parent_content_id": "BIGINT",
        "root_content_id": "BIGINT",
        "text_content": "TEXT",
        "language": "TEXT",
        "content_url": "TEXT",
        "published_at": "TIMESTAMPTZ",
        "edited_at": "TIMESTAMPTZ",
        "deleted_at": "TIMESTAMPTZ",
        "reply_count": "BIGINT",
        "like_count": "BIGINT",
        "repost_count": "BIGINT",
        "quote_count": "BIGINT",
        "view_count": "BIGINT",
        "is_sensitive": "BOOLEAN",
        "extra": "JSONB NOT NULL DEFAULT '{}'::JSONB",
        "captured_at": "TIMESTAMPTZ",
    },
    "social_media": {
        "content_id": "BIGINT NOT NULL",
        "media_index": "INTEGER NOT NULL",
        "media_type": "TEXT NOT NULL",
        "platform_media_id": "TEXT",
        "source_url": "TEXT",
        "url": "TEXT",
        "width": "INTEGER",
        "height": "INTEGER",
        "duration_ms": "BIGINT",
        "alt_text": "TEXT",
        "extra": "JSONB NOT NULL DEFAULT '{}'::JSONB",
    },
    "social_hashtag": {
        "hashtag_id": "BIGSERIAL NOT NULL",
        "hashtag_text": "TEXT NOT NULL",
        "normalized_text": "TEXT NOT NULL",
    },
    "social_content_hashtag": {
        "content_id": "BIGINT NOT NULL",
        "hashtag_id": "BIGINT NOT NULL",
        "hashtag_index": "INTEGER",
    },
    "social_interaction": {
        "interaction_id": "BIGSERIAL NOT NULL",
        "interaction_type": "TEXT NOT NULL",
        "actor_account_id": "BIGINT",
        "source_content_id": "BIGINT",
        "target_content_id": "BIGINT NOT NULL",
        "occurred_at": "TIMESTAMPTZ",
        "extra": "JSONB NOT NULL DEFAULT '{}'::JSONB",
        "captured_at": "TIMESTAMPTZ",
    },
    "news": {
        "title": "TEXT",
        "url": "TEXT",
        "source_url": "TEXT",
        "source_published_at": "TIMESTAMPTZ",
        "source_updated_at": "TIMESTAMPTZ",
        "summary": "TEXT",
        "content": "TEXT",
        "content_html": "TEXT",
        "summary_html": "TEXT",
        "author": "TEXT",
        "news_type": "JSONB",
        "organization": "JSONB",
        "tags": "JSONB",
        "external_links": "JSONB",
        "attachments": "JSONB",
        "images": "JSONB",
        "slides": "JSONB",
        "thumbnail": "TEXT",
        "videos": "JSONB",
        "iframes": "JSONB",
        "audios": "JSONB",
    },
    "patent": {
        "title": "TEXT",
        "publication_number": "TEXT",
        "application_number": "TEXT",
        "assignee": "TEXT",
        "inventor": "TEXT",
        "publication_date": "DATE",
        "filing_date": "DATE",
        "priority_date": "DATE",
        "grant_date": "DATE",
        "abstract": "TEXT",
        "claims": "JSONB",
        "legal_status": "TEXT",
        "ipc_classification": "TEXT",
        "cpc_classification": "TEXT",
        "patent_type": "TEXT",
        "url": "TEXT",
        "thumbnail": "TEXT",
        "figures": "JSONB",
    },
    "navwarn": {
        "navarea_id": "INTEGER",
        "warning_no": "TEXT",
        "serial_number": "INTEGER",
        "warning_year": "INTEGER",
        "region": "TEXT",
        "sub_region": "TEXT",
        "oceans": "TEXT",
        "dnc_region": "TEXT",
        "issued_at": "TIMESTAMPTZ",
        "message_text": "TEXT",
        "category": "TEXT",
        "status": "TEXT",
        "coordinate": "GEOGRAPHY(GEOMETRY, 4326)",
    },
    "intelligence": {
        "title": "TEXT",
        "url": "TEXT",
        "source_published_at": "TIMESTAMPTZ",
        "source_updated_at": "TIMESTAMPTZ",
        "summary": "TEXT",
        "file_name": "TEXT",
        "file_size": "TEXT",
        "file_type": "TEXT",
    },
    "filing": {
        "cik": "TEXT",
        "accession_number": "TEXT",
        "form": "TEXT",
        "filing_date": "DATE",
        "report_date": "DATE",
        "acceptance_datetime": "TIMESTAMPTZ",
        "act": "TEXT",
        "file_number": "TEXT",
        "film_number": "TEXT",
        "items": "TEXT",
        "core_type": "TEXT",
        "size": "BIGINT",
        "is_xbrl": "BOOLEAN",
        "is_inline_xbrl": "BOOLEAN",
        "filing_base": "TEXT",
        "filing_index_url": "TEXT",
    },
    "filing_document": {
        "cik": "TEXT",
        "accession_number": "TEXT",
        "sequence": "TEXT",
        "description": "TEXT",
        "filename": "TEXT",
        "size": "TEXT",
        "url": "TEXT",
        "source_url": "TEXT",
        "doc_type": "TEXT",
    },
    "constellation": {
        **_SPACE_COMMON_COLUMNS,
        "name": "TEXT",
        "status": "TEXT",
        "purpose": "TEXT",
        "owner": "TEXT",
        "country": "TEXT",
        "launched_count": "BIGINT",
        "planned_count": "BIGINT",
        "member_norad_ids": "JSONB",
        "first_launch_date": "DATE",
        "first_launch_date_raw": "TEXT",
    },
    "vulnerability": {
        "title": "TEXT",
        "content": "TEXT",
        "url": "TEXT",
        "cve_id": "TEXT",
        "status": "TEXT",
        "description": "TEXT",
        "source_published_at": "TIMESTAMPTZ",
        "source_updated_at": "TIMESTAMPTZ",
        "cvss_version": "TEXT",
        "cvss_vector": "TEXT",
        "cvss_base_score": "DOUBLE PRECISION",
        "cvss_base_severity": "TEXT",
        "descriptions": "JSONB",
        "references": "JSONB",
        "weaknesses": "JSONB",
        "metrics": "JSONB",
        "configurations": "JSONB",
        "affected": "JSONB",
        "cwe_ids": "JSONB",
        "attributes": "JSONB",
    },
    "debrisevents": {
        **_SPACE_COMMON_COLUMNS,
        "parent_name": "TEXT",
        "norad_id": "BIGINT",
        "cospar_id": "TEXT",
        "cospar_launch": "TEXT",
        "parent_gcat_id": "TEXT",
        "related_objects": "JSONB",
        "event_type": "TEXT",
        "event_date": "DATE",
        "event_date_raw": "TEXT",
        "cataloged_object_count": "BIGINT",
        "perigee_km": "DOUBLE PRECISION",
        "apogee_km": "DOUBLE PRECISION",
        "inclination_degrees": "DOUBLE PRECISION",
    },
    "debris": {
        **_SPACE_COMMON_COLUMNS,
        "name": "TEXT",
        "norad_id": "BIGINT",
        "cospar_id": "TEXT",
        "object_class": "TEXT",
        "epoch": "TIMESTAMPTZ",
        "epoch_raw": "TEXT",
    },
    "launchvehicles": {
        **_SPACE_COMMON_COLUMNS,
        "name": "TEXT",
        "family": "TEXT",
        "manufacturer": "TEXT",
        "manufacturer_codes": "JSONB",
        "variant": "TEXT",
    },
    "frequency": {
        **_SPACE_COMMON_COLUMNS,
        "satellite_name": "TEXT",
        "norad_id": "BIGINT",
        "cospar_id": "TEXT",
        "satnogs_sat_id": "TEXT",
        "itu_earth_notice_id": "TEXT",
        "notice_id": "TEXT",
        "direction": "TEXT",
        "frequency_from_hz": "DOUBLE PRECISION",
        "frequency_to_hz": "DOUBLE PRECISION",
        "uplink_low_hz": "DOUBLE PRECISION",
        "uplink_high_hz": "DOUBLE PRECISION",
        "downlink_low_hz": "DOUBLE PRECISION",
        "downlink_high_hz": "DOUBLE PRECISION",
    },
    "groundstations": {
        **_SPACE_COMMON_COLUMNS,
        "name": "TEXT",
        "organization": "TEXT",
        "country": "TEXT",
        "latitude": "DOUBLE PRECISION",
        "longitude": "DOUBLE PRECISION",
        "altitude_m": "DOUBLE PRECISION",
        "status": "TEXT",
        "bands": "JSONB",
        "location": "TEXT",
        "callsign": "TEXT",
        "itu_earth_notice_id": "TEXT",
    },
    "launchinfo": {
        **_SPACE_COMMON_COLUMNS,
        "mission_name": "TEXT",
        "rocket_name": "TEXT",
        "vehicle_variant": "TEXT",
        "provider": "TEXT",
        "provider_codes": "JSONB",
        "site_name": "TEXT",
        "site_code": "TEXT",
        "pad_name": "TEXT",
        "status": "TEXT",
        "cospar_launch": "TEXT",
        "launch_date": "DATE",
        "launch_date_raw": "TEXT",
    },
    "satellite": {
        **_SPACE_COMMON_COLUMNS,
        "name": "TEXT",
        "norad_id": "BIGINT",
        "cospar_id": "TEXT",
        "gcat_id": "TEXT",
        "satnogs_sat_id": "TEXT",
        "alternative_name": "TEXT",
        "owner": "TEXT",
        "owner_codes": "JSONB",
        "country": "TEXT",
        "manufacturer": "TEXT",
        "manufacturer_codes": "JSONB",
        "status": "TEXT",
        "launch_date": "DATE",
        "launch_date_raw": "TEXT",
        "cospar_launch": "TEXT",
        "rocket_name": "TEXT",
        "site_name": "TEXT",
        "orbit_class": "TEXT",
        "perigee_km": "DOUBLE PRECISION",
        "apogee_km": "DOUBLE PRECISION",
        "inclination_degrees": "DOUBLE PRECISION",
        "period_minutes": "DOUBLE PRECISION",
        "launch_mass_kg": "DOUBLE PRECISION",
        "dry_mass_kg": "DOUBLE PRECISION",
        "tle_line1": "TEXT",
        "tle_line2": "TEXT",
    },
    "launchsites": {
        **_SPACE_COMMON_COLUMNS,
        "name": "TEXT",
        "country": "TEXT",
        "site_code": "TEXT",
        "operator_codes": "JSONB",
        "pad_name": "TEXT",
        "site_type": "TEXT",
        "latitude": "DOUBLE PRECISION",
        "longitude": "DOUBLE PRECISION",
        "location": "TEXT",
        "operational_start_raw": "TEXT",
        "operational_stop_raw": "TEXT",
    },
    "orbital": {
        **_SPACE_COMMON_COLUMNS,
        "name": "TEXT",
        "norad_id": "BIGINT",
        "cospar_id": "TEXT",
        "epoch": "TIMESTAMPTZ",
        "epoch_raw": "TEXT",
        "tle_line0": "TEXT",
        "tle_line1": "TEXT",
        "tle_line2": "TEXT",
        "inclination_degrees": "DOUBLE PRECISION",
        "eccentricity": "DOUBLE PRECISION",
        "mean_motion": "DOUBLE PRECISION",
        "mean_anomaly_degrees": "DOUBLE PRECISION",
        "ra_of_asc_node_degrees": "DOUBLE PRECISION",
        "arg_of_pericenter_degrees": "DOUBLE PRECISION",
    },
    "organization": {
        **_SPACE_COMMON_COLUMNS,
        "name": "TEXT",
        "code": "TEXT",
        "country": "TEXT",
        "organization_type": "TEXT",
        "parent": "TEXT",
        "parent_codes": "JSONB",
        "latitude": "DOUBLE PRECISION",
        "longitude": "DOUBLE PRECISION",
        "operational_start_raw": "TEXT",
        "operational_stop_raw": "TEXT",
        "cik": "TEXT",
        "entity_type": "TEXT",
        "exchanges": "JSONB",
        "tickers": "JSONB",
        "sic": "TEXT",
        "sic_description": "TEXT",
        "address": "JSONB",
        "website": "TEXT",
        "ein": "TEXT",
        "category": "TEXT",
        "phone": "TEXT",
        "former_names": "JSONB",
        "investor_website": "TEXT",
        "fiscal_year_end": "TEXT",
        "incorporation_state": "TEXT",
    },
}

_RDS_COLUMNS = {
    **dict.fromkeys(RECORD_IDENTITY_COLUMNS, "TEXT NOT NULL"),
    "raw_data": "JSONB NOT NULL",
    "kafka_offset": "BIGINT",
    "kafka_partition": "INTEGER",
    "kafka_topic": "TEXT",
    **_AUDIT_COLUMNS,
}
_EXTRA_ODS_INDEXES = {
    "constellation": (
        ("idx_ods_constellation_member_norad_ids", "member_norad_ids", "member_norad_ids IS NOT NULL", "gin"),
    ),
    "vulnerability": (
        ("idx_ods_vulnerability_cve_id", "cve_id", "cve_id IS NOT NULL", "btree"),
    ),
    "satellite": (
        ("idx_ods_satellite_norad_id", "norad_id", "norad_id IS NOT NULL", "btree"),
        ("idx_ods_satellite_cospar_id", "cospar_id", "cospar_id IS NOT NULL", "btree"),
        ("idx_ods_satellite_gcat_id", "gcat_id", "gcat_id IS NOT NULL", "btree"),
        ("idx_ods_satellite_satnogs_sat_id", "satnogs_sat_id", "satnogs_sat_id IS NOT NULL", "btree"),
        ("idx_ods_satellite_owner_codes", "owner_codes", "owner_codes IS NOT NULL", "gin"),
        ("idx_ods_satellite_manufacturer_codes", "manufacturer_codes", "manufacturer_codes IS NOT NULL", "gin"),
    ),
    "organization": (
        ("idx_ods_organization_cik", "cik", None, "btree"),
        ("idx_ods_organization_code", "code", "code IS NOT NULL", "btree"),
    ),
    "launchinfo": (
        ("idx_ods_launchinfo_cospar_launch", "cospar_launch", "cospar_launch IS NOT NULL", "btree"),
    ),
    "launchsites": (
        ("idx_ods_launchsites_site_pad", "site_code, pad_name", "site_code IS NOT NULL", "btree"),
    ),
    "launchvehicles": (
        ("idx_ods_launchvehicles_name_variant", "name, variant", "name IS NOT NULL", "btree"),
    ),
    "groundstations": (
        ("idx_ods_groundstations_itu_earth_notice_id", "itu_earth_notice_id", "itu_earth_notice_id IS NOT NULL", "btree"),
    ),
    "debris": (
        ("idx_ods_debris_source_id", "source_id", "source_id IS NOT NULL AND data_source LIKE 'esa_discos%'", "btree"),
    ),
    "debrisevents": (
        ("idx_ods_debrisevents_related_objects", "related_objects", "related_objects IS NOT NULL", "gin"),
    ),
    "contract": (
        ("idx_ods_contract_business_id", "agency_code, contract_number, modification_number, transaction_number", None, "btree"),
        ("idx_ods_contract_award_date", "award_date DESC", None, "btree"),
        ("idx_ods_contract_awardee_uei", "awardee_uei", None, "btree"),
    ),
    "tender": (
        ("idx_ods_tender_notice_id", "data_source, notice_id", None, "btree"),
        ("idx_ods_tender_solicitation", "solicitation_number", None, "btree"),
        ("idx_ods_tender_deadline", "response_deadline", None, "btree"),
    ),
    "financial_fact": (
        ("idx_fin_fact_accn", "accn", None, "btree"),
        ("idx_fin_fact_cik_period", "cik, end_date DESC", None, "btree"),
        ("idx_fin_fact_concept", "taxonomy, concept", None, "btree"),
        ("idx_fin_fact_filed", "filed DESC", None, "btree"),
    ),
    "social_account": (
        ("idx_social_account_source_id", "data_source, platform_account_id", None, "btree"),
    ),
    "social_content": (
        ("idx_social_content_source_id", "data_source, platform_content_id, record_id", None, "btree"),
        ("idx_social_content_author_time", "author_account_id, published_at DESC", None, "btree"),
        ("idx_social_content_parent", "parent_content_id, published_at", None, "btree"),
    ),
    "social_media": (
        ("idx_social_media_content", "content_id, media_index", None, "btree"),
    ),
    "social_hashtag": (
        ("idx_social_hashtag_source", "data_source, normalized_text", None, "btree"),
    ),
    "social_content_hashtag": (
        ("idx_social_content_hashtag_content", "content_id, hashtag_id", None, "btree"),
    ),
    "social_interaction": (
        ("idx_social_interaction_target", "target_content_id, interaction_type", None, "btree"),
        ("idx_social_interaction_actor", "actor_account_id, occurred_at DESC", None, "btree"),
    ),
    "news": (
        ("idx_ods_news_published_at", "source_published_at DESC", None, "btree"),
    ),
    "patent": (
        ("idx_ods_patent_publication_date", "publication_date DESC", None, "btree"),
    ),
    "navwarn": (
        ("idx_ods_navwarn_issued_at", "issued_at DESC", None, "btree"),
        ("idx_ods_navwarn_coordinate", "coordinate", None, "gist"),
    ),
    "intelligence": (
        ("idx_ods_intelligence_published_at", "source_published_at DESC", None, "btree"),
    ),
    "filing": (
        ("idx_ods_filing_accession", "accession_number", None, "btree"),
        ("idx_ods_filing_cik", "cik", None, "btree"),
    ),
    "filing_document": (
        ("idx_ods_filing_document_accession", "accession_number", None, "btree"),
    ),
}


def validate_table_name(name: str) -> str:
    # Leave room for the layer prefix and hash-partition suffix in PostgreSQL's 63 bytes.
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", name):
        raise ValueError(f"Invalid ETL data_type: {name!r}")
    return name


def validate_ods_fields(tables: dict[str, dict[str, str]]) -> None:
    """Published fields declare types, never executable DDL fragments."""
    types = {"TEXT", "BIGINT", "INTEGER", "DOUBLE PRECISION", "NUMERIC", "BOOLEAN",
             "JSONB", "DATE", "TIMESTAMPTZ"}
    if not isinstance(tables, dict):
        raise ValueError("ODS_FIELDS must be a dictionary")
    for table, fields in tables.items():
        validate_table_name(table)
        if not isinstance(fields, dict) or not fields:
            raise ValueError(f"ODS_FIELDS[{table!r}] must contain business fields")
        for name, definition in fields.items():
            validate_table_name(name)
            if name in {*RECORD_IDENTITY_COLUMNS, *_AUDIT_COLUMNS}:
                raise ValueError(f"ODS identity/audit field is managed by ETL: {name}")
            if not isinstance(definition, str) or definition.upper() not in types:
                raise ValueError(f"Unsupported published ODS field type: {table}.{name}={definition!r}")


def ods_columns(table: str, fields: dict[str, str]) -> dict[str, str]:
    validate_table_name(table)
    return {
        **dict.fromkeys(RECORD_IDENTITY_COLUMNS, "TEXT NOT NULL"),
        "data_type": f"TEXT NOT NULL DEFAULT '{table}'",
        **{name: definition.upper() for name, definition in fields.items()},
        **_AUDIT_COLUMNS,
    }


TABLE_DEFINITIONS: dict[str, dict[str, TableDefinition]] = {
    "rds": {
        table: {
            "columns": _RDS_COLUMNS,
            "primary_key": RECORD_IDENTITY_COLUMNS,
            "partition_columns": RECORD_IDENTITY_COLUMNS,
            "indexes": (
                (f"idx_rds_{table}_data_source", "data_source", None, "btree"),
                (f"idx_rds_{table}_updated_at", "updated_at DESC", None, "btree"),
            ),
        }
        for table in (*_CATALOG_FIELDS, "account", "tweet")
    },
    "ods": {
        table: {
            "columns": ods_columns(table, columns),
            "primary_key": RECORD_IDENTITY_COLUMNS,
            "partition_columns": RECORD_IDENTITY_COLUMNS,
            "indexes": (
                (f"idx_ods_{table}_data_source", "data_source", None, "btree"),
                (f"idx_ods_{table}_updated_at", "updated_at DESC", None, "btree"),
                *_EXTRA_ODS_INDEXES.get(table, ()),
            ),
        }
        for table, columns in _CATALOG_FIELDS.items()
    },
}

CATALOG_ODS_COLUMNS: dict[str, dict[str, str]] = {
    table: TABLE_DEFINITIONS["ods"][table]["columns"] for table in _CATALOG_FIELDS
}


def table_columns(layer: str, table: str) -> dict[str, str]:
    validate_table_name(table)
    if layer == "rds":
        return dict(_RDS_COLUMNS)
    return dict(CATALOG_ODS_COLUMNS[table])


def current_table_ddl(
    layer: str, logical_table: str, columns: dict[str, str] | None = None,
) -> str:
    """Render built-in and published tables using the same keys and layout."""
    validate_table_name(logical_table)
    if layer not in {"rds", "ods"}:
        raise ValueError(f"Unsupported current-table layer: {layer}")
    definition = TABLE_DEFINITIONS[layer].get(logical_table, {})
    columns = columns or table_columns(layer, logical_table)
    table_ref = f"ts_{layer}.{layer}_{logical_table}"
    body = [f'    "{name}" {sql_type}' for name, sql_type in columns.items()]
    body.append(f"    PRIMARY KEY ({', '.join(RECORD_IDENTITY_COLUMNS)})")
    ddl = (f"CREATE TABLE IF NOT EXISTS {table_ref} (\n" + ",\n".join(body)
           + f"\n) PARTITION BY HASH ({', '.join(RECORD_IDENTITY_COLUMNS)});")
    indexes = definition.get("indexes", (
        (f"idx_{layer}_{logical_table}_data_source", "data_source", None, "btree"),
        (f"idx_{layer}_{logical_table}_updated_at", "updated_at DESC", None, "btree"),
    ))
    for name, expression, predicate, method in indexes:
        ddl += (f"\nCREATE INDEX IF NOT EXISTS {name} ON {table_ref} USING {method} ({expression})"
                + (f" WHERE {predicate}" if predicate else "") + ";")
    return ddl


def current_insert_sql(layer: str, table: str, columns: dict[str, str], *, row_count: int = 1) -> str:
    validate_table_name(table)
    if layer not in {"rds", "ods"}:
        raise ValueError(f"Unsupported current-table layer: {layer}")
    if not 1 <= row_count <= 50:
        raise ValueError("Current-table SQL requires 1 to 50 rows")
    names = ", ".join(f'"{column}"' for column in columns)
    rows = []
    for row in range(row_count):
        values = []
        for column, definition in columns.items():
            parameter = f"{column}_{row}" if row_count > 1 else column
            sql_type = definition.split()[0].lower()
            if sql_type.startswith("geography"):
                value = f"ST_GeogFromText(NULLIF(BTRIM(CAST(:{parameter} AS text)), ''))"
            elif sql_type in {"jsonb", "date", "timestamptz"}:
                value = f"CAST(:{parameter} AS {sql_type})"
            else:
                value = f":{parameter}"
            values.append(value)
        rows.append(f"({', '.join(values)})")
    updates = ", ".join(
        f'"{column}" = EXCLUDED."{column}"'
        for column in columns
        if column not in {*RECORD_IDENTITY_COLUMNS, "created_at"}
    )
    alias = " AS target" if layer == "rds" else ""
    guard = ""
    if layer == "rds":
        def version(alias: str) -> str:
            return (
                f"(COALESCE(({alias}.raw_data->'_meta'->>'updated_at')::timestamptz, '-infinity'::timestamptz), "
                f"COALESCE(({alias}.raw_data->'_meta'->>'download_version')::bigint, 0))"
            )
        guard = f"WHERE {version('target')} <= {version('EXCLUDED')}\n"
    return (
        f"INSERT INTO ts_{layer}.{layer}_{table}{alias} ({names}) VALUES {', '.join(rows)}\n"
        f"ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET {updates}\n"
        f"{guard}"
        "RETURNING *"
    )
