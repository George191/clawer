"""Shared column, key, partition and index definitions for catalog tables."""

from __future__ import annotations

from typing import TypedDict


class _TableDefinition(TypedDict):
    columns: dict[str, str]
    primary_key: tuple[str, ...]
    partition_columns: tuple[str, ...]


class TableDefinition(_TableDefinition, total=False):
    checks: tuple[str, ...]
    indexes: tuple[tuple[str, str, str | None], ...]


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
    "constellation": {
        **_SPACE_COMMON_COLUMNS,
        "name": "TEXT",
        "status": "TEXT",
        "purpose": "TEXT",
        "owner": "TEXT",
        "country": "TEXT",
        "launched_count": "BIGINT",
        "planned_count": "BIGINT",
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
        "variant": "TEXT",
    },
    "frequency": {
        **_SPACE_COMMON_COLUMNS,
        "satellite_name": "TEXT",
        "norad_id": "BIGINT",
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
    },
    "launchinfo": {
        **_SPACE_COMMON_COLUMNS,
        "mission_name": "TEXT",
        "rocket_name": "TEXT",
        "provider": "TEXT",
        "site_name": "TEXT",
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
        "alternative_name": "TEXT",
        "owner": "TEXT",
        "country": "TEXT",
        "manufacturer": "TEXT",
        "status": "TEXT",
        "launch_date": "DATE",
        "launch_date_raw": "TEXT",
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

RELATION_MATCH_INDEXES = (
    ("satellite", "norad", "norad_id", None),
    ("satellite", "cospar", "upper(btrim(cospar_id))", None),
    ("satellite", "satnogs_id", "source_id", None),
    ("satellite", "gcat_id", "btrim(attributes->>'jonathan_catid')", None),
    ("organization", "gcat_code", "btrim(code)", None),
    ("organization", "cve_uuid",
     "translate(replace(lower(attributes->>'cna_org_id'), 'urn:uuid:', ''), '{}-', '')", None),
    ("organization", "cpe_vendor", "record_id, data_source, data_type", "attributes ? 'cpe_vendor'"),
    ("launchinfo", "gcat_launch", "btrim(cospar_launch)", None),
    ("launchsites", "gcat_site_pad", "btrim(site_code), btrim(pad_name)", None),
    ("launchvehicles", "gcat_vehicle", "btrim(name), btrim(variant)", None),
    ("groundstations", "itu_notice", "btrim(attributes->>'ntc_id')", None),
    *((kind, "esa_id", "btrim(source_id)", None) for kind in (
        "satellite", "launchinfo", "launchvehicles", "launchsites", "organization", "debrisevents",
    )),
)

_RDS_COLUMNS = {
    **dict.fromkeys(RECORD_IDENTITY_COLUMNS, "TEXT NOT NULL"),
    "raw_data": "JSONB NOT NULL",
    "kafka_offset": "BIGINT",
    "kafka_partition": "INTEGER",
    "kafka_topic": "TEXT",
    **_AUDIT_COLUMNS,
}
_SOURCE_COLUMNS = ("source_record_id", "source_data_source", "source_data_type")
_TARGET_COLUMNS = ("target_record_id", "target_data_source", "target_data_type")
_EXTRA_ODS_INDEXES = {
    "organization": (("idx_ods_organization_cik", "cik", None),),
}

TABLE_DEFINITIONS: dict[str, dict[str, TableDefinition]] = {
    "rds": {
        table: {
            "columns": _RDS_COLUMNS,
            "primary_key": RECORD_IDENTITY_COLUMNS,
            "partition_columns": RECORD_IDENTITY_COLUMNS,
            "indexes": (
                (f"idx_rds_{table}_data_source", "data_source", None),
                (f"idx_rds_{table}_updated_at", "updated_at DESC", None),
            ),
        }
        for table in _CATALOG_FIELDS
    },
    "ods": {
        **{
            table: {
                "columns": {
                    **dict.fromkeys(RECORD_IDENTITY_COLUMNS, "TEXT NOT NULL"),
                    "data_type": f"TEXT NOT NULL DEFAULT '{table}'",
                    **columns,
                    **_AUDIT_COLUMNS,
                },
                "primary_key": RECORD_IDENTITY_COLUMNS,
                "partition_columns": RECORD_IDENTITY_COLUMNS,
                "indexes": (
                    (f"idx_ods_{table}_data_source", "data_source", None),
                    (f"idx_ods_{table}_updated_at", "updated_at DESC", None),
                    *_EXTRA_ODS_INDEXES.get(table, ()),
                    *((f"idx_ods_{table}_relation_{name}", expression, predicate)
                      for kind, name, expression, predicate in RELATION_MATCH_INDEXES if kind == table),
                ),
            }
            for table, columns in _CATALOG_FIELDS.items()
        },
        "entity_relation": {
            "columns": {
                **dict.fromkeys((*_SOURCE_COLUMNS, *_TARGET_COLUMNS), "TEXT NOT NULL"),
                "relation_name": "TEXT NOT NULL",
                "relation_type": "TEXT NOT NULL",
                "start_time": "TIMESTAMPTZ",
                "end_time": "TIMESTAMPTZ",
                "description": "TEXT",
                "attributes": "JSONB NOT NULL DEFAULT '{}'",
                **_AUDIT_COLUMNS,
                "created_by": "TEXT",
                "updated_by": "TEXT",
            },
            "primary_key": (*_SOURCE_COLUMNS, "relation_type", *_TARGET_COLUMNS),
            "partition_columns": _SOURCE_COLUMNS,
            "checks": ("start_time IS NULL OR end_time IS NULL OR end_time >= start_time",),
            "indexes": (("idx_ods_entity_relation_target", ", ".join(_TARGET_COLUMNS), None),),
        },
        # Resolver state: retain business references so late targets and changed
        # identifiers can refresh edges; resolved_count = 0 means pending.
        "entity_relation_reference": {
            "columns": {
                **dict.fromkeys((*RECORD_IDENTITY_COLUMNS, "relation_type", "target_type",
                                 "target_namespace", "target_identifier"), "TEXT NOT NULL"),
                "resolved_count": "INTEGER NOT NULL DEFAULT 0",
            },
            "primary_key": (*RECORD_IDENTITY_COLUMNS, "relation_type", "target_type",
                            "target_namespace", "target_identifier"),
            "partition_columns": RECORD_IDENTITY_COLUMNS,
            "indexes": (("idx_ods_entity_relation_reference_target",
                         "target_type, target_namespace, target_identifier", None),),
        },
    },
}

CATALOG_ODS_COLUMNS: dict[str, dict[str, str]] = {
    table: TABLE_DEFINITIONS["ods"][table]["columns"] for table in _CATALOG_FIELDS
}


def current_table_ddl(layer: str, logical_table: str) -> str:
    """Render every declared current table through the same DDL path."""
    definition = TABLE_DEFINITIONS[layer][logical_table]
    table_ref = f"ts_{layer}.{layer}_{logical_table}"
    body = [f'    "{name}" {sql_type}' for name, sql_type in definition["columns"].items()]
    body.append(f"    PRIMARY KEY ({', '.join(definition['primary_key'])})")
    body.extend(f"    CHECK ({condition})" for condition in definition.get("checks", ()))
    ddl = (f"CREATE TABLE IF NOT EXISTS {table_ref} (\n" + ",\n".join(body)
           + f"\n) PARTITION BY HASH ({', '.join(definition['partition_columns'])});")
    for name, expression, predicate in definition.get("indexes", ()):
        ddl += (f"\nCREATE INDEX IF NOT EXISTS {name} ON {table_ref} ({expression})"
                + (f" WHERE {predicate}" if predicate else "") + ";")
    return ddl
