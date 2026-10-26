"""Record-to-record ODS relations and retained references awaiting resolution."""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy import text

from app.etl.relation_targets import (
    resolve_reference_targets,
    target_reference_keys,
    verify_source_identities,
)

ENTITY_RELATION_DDL = '''CREATE TABLE "ts_ods"."ods_entity_relation" (
    source_record_id TEXT NOT NULL,
    source_data_source TEXT NOT NULL,
    source_data_type TEXT NOT NULL,
    target_record_id TEXT NOT NULL,
    target_data_source TEXT NOT NULL,
    target_data_type TEXT NOT NULL,
    relation_name TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    description TEXT,
    attributes JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT,
    updated_by TEXT,
    PRIMARY KEY (source_record_id, source_data_source, source_data_type, relation_type,
                 target_record_id, target_data_source, target_data_type),
    CHECK (start_time IS NULL OR end_time IS NULL OR end_time >= start_time)
) PARTITION BY HASH (source_record_id, source_data_source, source_data_type);
CREATE INDEX IF NOT EXISTS idx_ods_entity_relation_target
    ON ts_ods.ods_entity_relation (target_record_id, target_data_source, target_data_type);'''

ENTITY_RELATION_REFERENCE_DDL = '''CREATE TABLE "ts_ods"."ods_entity_relation_reference" (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_namespace TEXT NOT NULL,
    target_identifier TEXT NOT NULL,
    resolved_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (record_id, data_source, data_type, relation_type, target_type, target_namespace, target_identifier)
) PARTITION BY HASH (record_id, data_source, data_type);
CREATE INDEX IF NOT EXISTS idx_ods_entity_relation_reference_target
    ON ts_ods.ods_entity_relation_reference (target_type, target_namespace, target_identifier);'''

FINANCIAL_LINK_CLEANUP_SQL = {
    "entity_relation_reference": '''DELETE FROM ts_ods.ods_entity_relation_reference
        WHERE data_type IN ('filing', 'financial_fact') AND relation_type = 'filed_by'
          AND target_type = 'organization' AND target_namespace = 'cik'
    ''',
}

_RELATION_NAMES = {
    "describes_satellite": "描述卫星", "has_satellite": "包含卫星",
    "describes_station": "描述地面站", "owned_by": "所属组织", "manufactured_by": "制造方",
    "launched_on": "所属发射事件", "part_of": "隶属", "operated_by": "运营方",
    "launched_from": "发射场", "launched_from_pad": "发射工位", "uses_vehicle": "使用运载火箭",
    "fragmented_from": "碎裂来源", "provided_by": "提供方", "assigned_by": "分配方",
    "affects_vendor": "影响厂商", "related_object": "关联对象",
}


_CATALOG_TYPES = frozenset({
    "constellation", "vulnerability", "debrisevents", "debris", "launchvehicles",
    "frequency", "groundstations", "launchinfo", "satellite", "launchsites", "orbital",
    "organization",
})
_ESA_TYPES = {
    "objects": "satellite", "launches": "launchinfo", "launchVehicles": "launchvehicles",
    "launchSites": "launchsites",
    "fragmentations": "debrisevents",
}


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _identifier(value: Any) -> str | None:
    if value is None or isinstance(value, bool | dict | list | tuple):
        return None
    value = str(value).strip()
    return None if value.lower() in {"", "-", "?", "n/a", "none", "null", "unknown"} else value


def _norad(value: Any) -> str | None:
    value = _identifier(value)
    return str(int(value)) if value and value.isascii() and value.isdigit() and int(value) > 0 else None


def _cospar(value: Any) -> str | None:
    value = (_identifier(value) or "").upper()
    return value if re.fullmatch(r"\d{4}-\d{3}[A-Z]+", value) else None


def _uuid(value: Any) -> str | None:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _compound(*values: Any) -> str | None:
    parts = [_identifier(value) for value in values]
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":")) if all(parts) else None


def build_entity_relations(
    raw_record: dict[str, Any], normalized_row: dict[str, Any],
) -> list[dict[str, str]]:
    """Return explicit references, without resolving display names."""
    row = normalized_row
    kind = row.get("data_type")
    if kind not in _CATALOG_TYPES:
        return []
    identity = {key: row.get(key) for key in ("record_id", "data_source", "data_type")}
    if not all(identity.values()):
        return []
    record = raw_record
    source = str(record.get("source") or row["data_source"])
    template = str(_mapping(record.get("_meta")).get("template") or row["data_source"])
    gcat_names = ("satellite", "organization", "organizations", "launchinfo", "launchsites", "launchvehicles", "debrisevents")
    is_gcat = any(value.startswith("jonathan") or value.startswith(tuple(f"{name}_jonathan" for name in gcat_names))
                  for value in (source, template))
    provider = "gcat" if is_gcat else "satnogs" if source.lower().startswith("satnogs") else source
    relations: list[dict[str, str]] = []

    def link(relation: str, target: str, namespace: str, value: Any) -> None:
        value = _identifier(value)
        item = {**identity, "relation_type": relation, "target_type": target,
                "target_namespace": namespace, "target_identifier": value}
        if value and item not in relations:
            relations.append(item)

    if kind in {"debris", "debrisevents", "orbital", "frequency"}:
        link("describes_satellite", "satellite", "norad", _norad(record.get("norad_id") or row.get("norad_id")))
        link("describes_satellite", "satellite", "cospar", _cospar(record.get("cospar_id") or row.get("cospar_id")))
    if kind == "constellation":
        satellites = record.get("sats_included")
        if not isinstance(satellites, list):
            satellites = record.get("satellites")
        for satellite in satellites if isinstance(satellites, list) else []:
            link("has_satellite", "satellite", "norad", _norad(_mapping(satellite).get("norad_id")))
    if kind == "frequency" and provider == "satnogs":
        link("describes_satellite", "satellite", "source:satnogs", record.get("sat_id"))
    if kind in {"groundstations", "frequency"} and (source.startswith("itu_") or template.startswith("itu_")):
        # Earth API sns_notice_id and satellite API ntc_id are separate ID
        # domains despite the collectors' shared canonical field name.
        notice = record.get("ntc_id") or row.get("notice_id")
        earth_notice = kind == "groundstations" or (
            (source == "itu_wic" or template == "itu_wic")
            and (record.get("ntc_type") or _mapping(record.get("com_el")).get("ntc_type")) == "S"
        )
        if kind == "frequency" and earth_notice:
            link("describes_station", "groundstations", "itu:earth_notice", notice)

    if is_gcat:
        if kind == "satellite":
            for field, relation in (("owner", "owned_by"), ("manufacturer", "manufactured_by")):
                for code in (_identifier(record.get(field)) or "").split("/"):
                    link(relation, "organization", "gcat:organization", code)
            cospar = _cospar(record.get("cospar_id") or row.get("cospar_id"))
            link("launched_on", "launchinfo", "cospar:launch", cospar[:8] if cospar else None)
        elif kind == "organization":
            link("part_of", "organization", "gcat:organization", record.get("parent") or row.get("parent"))
        elif kind == "launchinfo":
            link("operated_by", "organization", "gcat:organization", record.get("provider"))
            link("launched_from", "launchsites", "gcat:site", record.get("site_name"))
            link("launched_from_pad", "launchsites", "gcat:pad", _compound(record.get("site_name"), record.get("pad_name")))
            link("uses_vehicle", "launchvehicles", "gcat:vehicle", _compound(record.get("rocket_name"), record.get("lv_variant")))
        elif kind == "launchsites":
            site = record.get("site_code") or row.get("site_code")
            pad = record.get("pad_name") or row.get("pad_name")
            if pad:
                link("part_of", "launchsites", "gcat:site", site)
            link("operated_by", "organization", "gcat:organization", record.get("parent_org"))
        elif kind == "launchvehicles":
            link("manufactured_by", "organization", "gcat:organization", record.get("lv_manufacturer"))
        elif kind == "debrisevents":
            debris = _mapping(record.get("debris"))
            link("fragmented_from", "satellite", "gcat:satellite", record.get("parent_jonathan_catid") or debris.get("parent_jonathan_catid"))
            link("fragmented_from", "satellite", "cospar", _cospar(record.get("launch_designation") or debris.get("launch_designation")))

    if source == "esa_discos" or template.startswith("esa_discos"):
        for relation, relationship in _mapping(record.get("relationships")).items():
            data = _mapping(relationship).get("data")
            for target in data if isinstance(data, list) else [data]:
                target = _mapping(target)
                resource_type = target.get("type")
                target_kind = _ESA_TYPES.get(resource_type)
                if target_kind:
                    link(f"related_{relation}", target_kind, f"esa_discos:{resource_type}", target.get("id"))
        for target in record.get("related_objects") or []:
            target = _mapping(target)
            resource_type = target.get("resource_type")
            target_kind = _ESA_TYPES.get(resource_type)
            if target_kind:
                link("related_object", target_kind, f"esa_discos:{resource_type}", target.get("source_id"))
            if target_kind == "satellite":
                link("related_object", "satellite", "norad", _norad(target.get("norad_id")))
                link("related_object", "satellite", "cospar", _cospar(target.get("cospar_id")))

    if kind == "vulnerability":
        native = _mapping(record.get("raw"))
        containers = _mapping(record.get("containers")) or _mapping(native.get("containers"))
        for container in [_mapping(containers.get("cna")), *containers.get("adp", [])]:
            metadata = _mapping(_mapping(container).get("provider_metadata"))
            link("provided_by", "organization", "cve:organization", _uuid(metadata.get("org_id")))
        metadata = _mapping(record.get("cve_metadata")) or _mapping(native.get("cve_metadata"))
        link("assigned_by", "organization", "cve:organization", _uuid(metadata.get("assigner_org_id")))
        nvd = _mapping(record.get("cve")) or _mapping(native.get("cve"))

        def cpe_vendors(node: Any) -> None:
            if isinstance(node, dict):
                for value in node.values():
                    cpe_vendors(value)
            elif isinstance(node, list):
                for value in node:
                    cpe_vendors(value)
            elif isinstance(node, str) and node.startswith("cpe:2.3:"):
                components = re.split(r"(?<!\\):", node)
                if len(components) == 13 and components[3] not in {"*", "-", ""}:
                    link("affects_vendor", "organization", "cpe:vendor", components[3].lower())

        cpe_vendors(record.get("configurations", nvd.get("configurations")))
        for container in [_mapping(containers.get("cna")), *containers.get("adp", [])]:
            for affected in _mapping(container).get("affected") or []:
                cpe_vendors(_mapping(affected).get("cpes"))
        for affected in record.get("affected") or []:
            cpe_vendors(_mapping(affected).get("cpes"))
    return relations


def plan_entity_edges(
    references: list[dict[str, Any]],
    targets: dict[tuple[str, str, str], list[dict[str, str]]],
    existing_sources: set[tuple[str, str, str]],
    *, actor: str = "etl",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Plan record edges without writes, retaining every unresolved reference."""
    identity_columns = ("record_id", "data_source", "data_type")
    edges: dict[tuple[str, ...], dict[str, Any]] = {}
    counts = []
    for reference in references:
        source_key = tuple(reference[name] for name in identity_columns)
        target_key = tuple(reference[name] for name in ("target_type", "target_namespace", "target_identifier"))
        matches = targets.get(target_key, []) if source_key in existing_sources else []
        counts.append({**reference, "resolved_count": len(matches)})
        for target in matches:
            edge_key = (*source_key, reference["relation_type"], *(target[name] for name in identity_columns))
            edge = edges.setdefault(edge_key, {
                **{f"source_{name}": reference[name] for name in identity_columns},
                **{f"target_{name}": target[name] for name in identity_columns},
                "relation_type": reference["relation_type"],
                "relation_name": _RELATION_NAMES.get(reference["relation_type"], reference["relation_type"]),
                "references": set(),
            })
            edge["references"].add((reference["target_namespace"], reference["target_identifier"]))
    planned = []
    for edge in edges.values():
        evidence = [{"namespace": namespace, "identifier": identifier}
                    for namespace, identifier in sorted(edge.pop("references"))]
        planned.append({**edge, "attributes": json.dumps({"references": evidence}, ensure_ascii=False), "actor": actor})
    return planned, counts


ENTITY_EDGE_UPSERT_SQL = """
INSERT INTO ts_ods.ods_entity_relation AS existing (
    source_record_id, source_data_source, source_data_type,
    target_record_id, target_data_source, target_data_type,
    relation_name, relation_type, attributes, created_by, updated_by
) VALUES (
    :source_record_id, :source_data_source, :source_data_type,
    :target_record_id, :target_data_source, :target_data_type,
    :relation_name, :relation_type, CAST(:attributes AS jsonb), :actor, :actor
) ON CONFLICT (source_record_id, source_data_source, source_data_type, relation_type,
               target_record_id, target_data_source, target_data_type)
DO UPDATE SET relation_name = EXCLUDED.relation_name,
    attributes = existing.attributes || EXCLUDED.attributes,
    updated_at = NOW(), updated_by = EXCLUDED.updated_by
WHERE existing.relation_name IS DISTINCT FROM EXCLUDED.relation_name
   OR existing.attributes IS DISTINCT FROM existing.attributes || EXCLUDED.attributes
"""


async def upsert_entity_edges(session: Any, edges: list[dict[str, Any]]) -> None:
    """Preserve creation audit and business attributes when evidence changes."""
    if edges:
        await session.execute(text(ENTITY_EDGE_UPSERT_SQL), edges)


_IDENTITY_COLUMNS = ("record_id", "data_source", "data_type")
_SOURCE_KEYS_SQL = """jsonb_to_recordset(CAST(:source_keys AS jsonb))
    AS k(record_id text, data_source text, data_type text)"""
RELATION_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtext('ts_ods.entity_relations'))"


def _identity(row: dict[str, Any]) -> tuple[str, str, str]:
    return tuple(row[name] for name in _IDENTITY_COLUMNS)


def _source_keys_json(keys: list[tuple[str, str, str]]) -> str:
    return json.dumps([dict(zip(_IDENTITY_COLUMNS, key, strict=True)) for key in keys])


ENTITY_STALE_EDGES_DELETE_SQL = f"""
WITH stale AS MATERIALIZED (
    SELECT r.source_record_id, r.source_data_source, r.source_data_type, r.relation_type,
           r.target_record_id, r.target_data_source, r.target_data_type
    FROM ts_ods.ods_entity_relation r JOIN {_SOURCE_KEYS_SQL}
      ON (r.source_record_id, r.source_data_source, r.source_data_type) =
         (k.record_id, k.data_source, k.data_type)
    EXCEPT
    SELECT d.source_record_id, d.source_data_source, d.source_data_type, d.relation_type,
           d.target_record_id, d.target_data_source, d.target_data_type
    FROM jsonb_to_recordset(CAST(:desired AS jsonb)) AS d(
          source_record_id text, source_data_source text, source_data_type text,
          target_record_id text, target_data_source text, target_data_type text, relation_type text)
)
DELETE FROM ts_ods.ods_entity_relation r USING stale s
WHERE (r.source_record_id, r.source_data_source, r.source_data_type, r.relation_type,
       r.target_record_id, r.target_data_source, r.target_data_type) =
      (s.source_record_id, s.source_data_source, s.source_data_type, s.relation_type,
       s.target_record_id, s.target_data_source, s.target_data_type)
"""


async def delete_stale_entity_edges(
    session: Any, source_keys: list[tuple[str, str, str]], desired: list[dict[str, Any]],
) -> None:
    """Delete only specified sources' edges absent from their complete desired set."""
    if source_keys:
        await session.execute(text(ENTITY_STALE_EDGES_DELETE_SQL), {
            "source_keys": _source_keys_json(source_keys), "desired": json.dumps(desired),
        })


async def refresh_entity_relations(
    session: Any, source_keys: list[tuple[str, str, str]], *, actor: str = "etl",
) -> dict[str, int]:
    """Resolve a complete set of sources with the tested edge planning and SQL."""
    keys = sorted(set(source_keys))
    if not keys:
        return {"references": 0, "relations": 0, "pending_references": 0}
    result = await session.execute(text(f"""
        SELECT r.* FROM ts_ods.ods_entity_relation_reference r JOIN {_SOURCE_KEYS_SQL}
          ON (r.record_id, r.data_source, r.data_type) = (k.record_id, k.data_source, k.data_type)
    """), {"source_keys": _source_keys_json(keys)})
    references = [dict(row) for row in result.mappings().all()]
    existing_sources = await verify_source_identities(session, keys)
    targets = await resolve_reference_targets(session, references)
    desired, counts = plan_entity_edges(references, targets, existing_sources, actor=actor)
    await upsert_entity_edges(session, desired)
    await delete_stale_entity_edges(session, keys, desired)
    if counts:
        await session.execute(text("""
            UPDATE ts_ods.ods_entity_relation_reference r SET resolved_count = k.resolved_count
            FROM jsonb_to_recordset(CAST(:counts AS jsonb)) AS k(
                record_id text, data_source text, data_type text, relation_type text,
                target_type text, target_namespace text, target_identifier text, resolved_count integer)
            WHERE (r.record_id, r.data_source, r.data_type, r.relation_type,
                   r.target_type, r.target_namespace, r.target_identifier) =
                  (k.record_id, k.data_source, k.data_type, k.relation_type,
                   k.target_type, k.target_namespace, k.target_identifier)
              AND r.resolved_count IS DISTINCT FROM k.resolved_count
        """), {"counts": json.dumps(counts)})
    return {"references": len(references), "relations": len(desired),
            "pending_references": sum(row["resolved_count"] == 0 for row in counts)}


async def write_entity_links_batch(
    session: Any, records: list[tuple[dict[str, Any], dict[str, Any]]], *, actor: str = "etl",
) -> dict[str, int]:
    """Maintain source references and incoming record edges in one transaction."""
    current = {_identity(row): (raw, row) for raw, row in records
               if row.get("data_type") in _CATALOG_TYPES
               and all(row.get(name) for name in _IDENTITY_COLUMNS)}
    if not current:
        return {"references": 0, "relations": 0, "pending_references": 0}
    await session.execute(text(RELATION_LOCK_SQL))
    sources_json = _source_keys_json(list(current))
    result = await session.execute(text(f"""
        SELECT DISTINCT r.source_record_id AS record_id, r.source_data_source AS data_source,
                        r.source_data_type AS data_type
        FROM ts_ods.ods_entity_relation r JOIN {_SOURCE_KEYS_SQL}
          ON (r.target_record_id, r.target_data_source, r.target_data_type) =
             (k.record_id, k.data_source, k.data_type)
    """), {"source_keys": sources_json})
    affected = set(current) | {_identity(dict(row)) for row in result.mappings().all()}
    references = [ref for raw, row in current.values() for ref in build_entity_relations(raw, row)]
    await session.execute(text(f"""
        DELETE FROM ts_ods.ods_entity_relation_reference r USING {_SOURCE_KEYS_SQL}
        WHERE (r.record_id, r.data_source, r.data_type) = (k.record_id, k.data_source, k.data_type)
    """), {"source_keys": sources_json})
    if references:
        await session.execute(text("""
            INSERT INTO ts_ods.ods_entity_relation_reference
                (record_id, data_source, data_type, relation_type, target_type, target_namespace, target_identifier)
            VALUES (:record_id, :data_source, :data_type, :relation_type, :target_type, :target_namespace, :target_identifier)
            ON CONFLICT DO NOTHING
        """), references)
    target_keys = sorted({key for raw, row in current.values() for key in target_reference_keys(raw, row)})
    if target_keys:
        result = await session.execute(text("""
            SELECT DISTINCT r.record_id, r.data_source, r.data_type
            FROM ts_ods.ods_entity_relation_reference r
            JOIN jsonb_to_recordset(CAST(:targets AS jsonb)) AS k(
                target_type text, target_namespace text, target_identifier text)
              ON (r.target_type, r.target_namespace, r.target_identifier) =
                 (k.target_type, k.target_namespace, k.target_identifier)
        """), {"targets": json.dumps([dict(zip(("target_type", "target_namespace", "target_identifier"), key, strict=True))
                                     for key in target_keys])})
        affected.update(_identity(dict(row)) for row in result.mappings().all())
    return await refresh_entity_relations(session, sorted(affected), actor=actor)


async def write_entity_links(session: Any, raw_record: dict[str, Any], normalized_row: dict[str, Any]) -> None:
    """Maintain references and record edges in the caller's transaction."""
    await write_entity_links_batch(session, [(raw_record, normalized_row)])
