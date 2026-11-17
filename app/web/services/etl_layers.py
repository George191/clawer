"""PostgreSQL catalog and bounded data access for the Flow workspace."""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from anyio import fail_after
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.etl.schema.object_relations import (
    ObjectRelationsNotInitialized,
    merge_object_relations,
    read_object_relations,
)
from app.storage.postgres_client import get_pg_client
from app.web.api.models.etl import PREVIEW_ROWS_MAX_LIMIT, PREVIEW_ROWS_MAX_OFFSET
from app.web.services.index_preview_sandbox import INDEX_SANDBOX_SCHEMA, INDEX_SANDBOX_TABLE
from app.web.services.synthetic_governance import PROTECTED_SYNTHETIC_SCHEMA

LAYERS = ("rds", "ods", "task", "dwd", "dws", "dim", "ads")
_OBJECT_GROUPS = ("ingestion", "processing", "serving", "other")
_OBJECT_GROUP_SQL = "COALESCE(o.resource_group, 'other')"
_OBJECT_METADATA_JOIN = """LEFT JOIN public.ontology_objects o
    ON o.schema_name = n.nspname AND o.table_name = c.relname AND o.is_active"""
_OBJECT_DESCRIPTION_SQL = "COALESCE(NULLIF(obj_description(c.oid, 'pg_class'), ''), o.description, '')"
_OBJECT_TABLE_SQL = f"""n.nspname ~ '^ts_[a-z0-9_]+$' AND n.nspname <> 'ts_meta'
    AND n.nspname <> 'ts_meta_hist' AND n.nspname <> '{PROTECTED_SYNTHETIC_SCHEMA}'
    AND c.relname ~ '^[a-z_][a-z0-9_]{{0,62}}$' AND c.relkind IN ('r','p')
    AND NOT c.relispartition
    AND NOT EXISTS (SELECT 1 FROM pg_inherits child WHERE child.inhrelid = c.oid)"""
_TRANSFER_ENDPOINT_WHERE = f"""{_OBJECT_TABLE_SQL} AND COALESCE(o.visibility, 'normal') <> 'hidden' AND (
    (n.nspname = :source_schema AND c.relname = :source_table) OR
    (n.nspname = :target_schema AND c.relname = :target_table))"""
_FINANCIAL_ODS = ("ts_ods", "ods_financial_fact")
_LATEST_FILED_SQL = (
    "SELECT filed FROM ts_ods.ods_financial_fact "
    "WHERE filed IS NOT NULL ORDER BY filed DESC LIMIT 1"
)


def _bounded_filed_plan(explained: Any) -> bool:
    """Accept only a single-row Limit and a bounded fan-out of ordered index reads."""
    if isinstance(explained, str):
        try:
            explained = json.loads(explained)
        except ValueError:
            return False
    if not isinstance(explained, list) or len(explained) != 1 or not isinstance(explained[0], dict):
        return False
    root = explained[0].get("Plan")
    if not isinstance(root, dict) or root.get("Node Type") != "Limit" or root.get("Plan Rows") != 1:
        return False
    pending = root.get("Plans")
    if not isinstance(pending, list) or len(pending) != 1:
        return False
    pending = list(pending)
    visited = 0
    while pending:
        node = pending.pop()
        visited += 1
        if visited > 128 or not isinstance(node, dict) or "Filter" in node or "Recheck Cond" in node:
            return False
        children = node.get("Plans", [])
        if not isinstance(children, list):
            return False
        node_type = node.get("Node Type")
        if node_type in ("Index Scan", "Index Only Scan"):
            if children or not node.get("Index Name"):
                return False
            condition = node.get("Index Cond")
            if condition is not None and (not isinstance(condition, str) or not re.fullmatch(
                r'\((?:"?[A-Za-z0-9_]+"?\.)?"?filed"? IS NOT NULL\)', condition
            )):
                return False
        elif node_type == "Merge Append":
            if not children or len(children) + len(pending) + visited > 128:
                return False
            pending.extend(children)
        else:
            return False
    return True


def quote_identifier(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name):
        raise HTTPException(400, "Invalid PostgreSQL identifier")
    return f'"{name}"'


def resolve_schema(layer: str, table_role: str = "current") -> str:
    if table_role not in {"current", "history"}:
        raise HTTPException(400, "Unsupported table role")
    schema = layer if layer.startswith("ts_") else f"ts_{layer}"
    quote_identifier(schema)
    if schema == PROTECTED_SYNTHETIC_SCHEMA:
        raise HTTPException(404, "Schema not found")
    if table_role == "history" and not schema.endswith("_hist"):
        schema += "_hist"
    quote_identifier(schema)
    return schema


def _object_schema(layer: str) -> str:
    schema = resolve_schema(layer)
    if schema in {"ts_meta", "ts_meta_hist"}:
        raise HTTPException(404, "Schema not found")
    return schema


def _object_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f'{row["schema"]}.{row["name"]}', "schema": row["schema"],
        "table": row["name"], "name": row["name"], "displayName": row["name"],
        "description": row["description"] or "", "dataType": row["data_type"] or "",
        "dataTypeSource": row.get("data_type_source") or "not_configured",
        "descriptionSource": row.get("description_source") or "not_configured",
        "resourceGroup": row.get("resource_group") or "other",
        "objectCount": int(row["object_count"]), "objectCountEstimated": True,
        "propertiesCount": int(row["properties_count"]), "status": "available",
        "titleProperty": None, "visibility": row["visibility"],
    }


class EtlLayers:
    def __init__(self) -> None:
        self._pg = get_pg_client()

    @asynccontextmanager
    async def session(self):
        try:
            with fail_after(12):
                await self._pg.connect(max_retries=1)
                async with self._pg.session() as session:
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                    await session.execute(text("SET LOCAL statement_timeout = '10s'"))
                    yield session
        except HTTPException:
            raise
        except (TimeoutError, ConnectionError, OSError) as exc:
            raise HTTPException(503, "PostgreSQL is unavailable or the query timed out") from exc
        except DBAPIError as exc:
            code = getattr(exc.orig, "sqlstate", None)
            if code == "42P01":
                raise HTTPException(503, "Ontology metadata is not initialized") from exc
            status = 503 if code == "57014" else 400
            raise HTTPException(status, "PostgreSQL could not complete this query") from exc

    async def layers(self) -> list[dict[str, Any]]:
        async with self.session() as session:
            result = await session.execute(text(r"""
                SELECT n.nspname AS schema, count(c.oid) AS tables,
                       obj_description(n.oid, 'pg_namespace') AS alias,
                       current_database() AS database
                FROM pg_namespace n LEFT JOIN pg_class c
                  ON c.relnamespace = n.oid AND c.relkind IN ('r','p')
                     AND NOT c.relispartition
                WHERE n.nspname LIKE 'ts\_%' ESCAPE '\' AND n.nspname <> :restricted
                GROUP BY n.oid, n.nspname ORDER BY n.nspname
            """), {"restricted": PROTECTED_SYNTHETIC_SCHEMA})
            found = {row["schema"]: dict(row) for row in result.mappings()}
        schemas = list(dict.fromkeys([*(f"ts_{layer}" for layer in LAYERS), *found]))
        return [{
            "key": schema[3:] if schema[3:] in LAYERS else schema,
            "label": schema[3:].upper(), "schemaName": schema,
            "alias": found.get(schema, {}).get("alias") or "",
            "database": found.get(schema, {}).get("database", ""),
            "icon": "database", "status": "running" if schema in found else "stopped",
            "rateIn": 0, "rateOut": 0, "lag": 0,
            "metricsAvailable": False, "tables": found.get(schema, {}).get("tables", 0),
        } for schema in schemas]

    async def tables(self, layer: str, table_role: str = "current") -> list[dict[str, Any]]:
        schema = resolve_schema(layer, table_role)
        async with self.session() as session:
            result = await session.execute(text(f"""
                SELECT c.relname AS name, c.relkind = 'p' OR storage.has_children AS partitioned,
                       o.data_type, {_OBJECT_DESCRIPTION_SQL} AS description,
                       storage.rows, storage.size
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                {_OBJECT_METADATA_JOIN}
                CROSS JOIN LATERAL (
                    WITH RECURSIVE descendants(relid) AS (
                        SELECT c.oid
                        UNION
                        SELECT i.inhrelid FROM pg_inherits i
                        JOIN descendants d ON i.inhparent = d.relid
                    )
                    SELECT COALESCE(sum(CASE WHEN physical.relkind = 'p' THEN 0
                                             ELSE GREATEST(0, pg_stat_get_live_tuples(d.relid)) END), 0) AS rows,
                           pg_size_pretty(sum(pg_total_relation_size(d.relid))) AS size,
                           bool_or(d.relid <> c.oid) AS has_children
                    FROM descendants d JOIN pg_class physical ON physical.oid = d.relid
                ) storage
                WHERE n.nspname = :schema AND c.relkind IN ('r','p')
                  AND NOT c.relispartition ORDER BY c.relname
            """), {"schema": schema})
            rows = result.mappings().all()
        return [{"name": row["name"], "schemaName": schema, "dataType": row["data_type"] or "",
                 "tableRole": "history" if schema.endswith("_hist") else "current",
                 "partitioned": row["partitioned"], "rowCount": int(row["rows"]),
                 "size": row["size"], "updatedAt": "", "description": row["description"] or "",
                 "rowCountEstimated": True} for row in rows]

    @staticmethod
    async def _require_ontology(session) -> None:
        installed = (await session.execute(text(
            "SELECT to_regclass('public.ontology_objects') IS NOT NULL "
            "AND to_regclass('public.ontology_object_properties') IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = to_regclass('public.ontology_objects') "
            "AND attname = 'visibility' AND attnum > 0 AND NOT attisdropped)"
        ))).scalar_one()
        if not installed:
            raise HTTPException(503, "Ontology metadata is not initialized")

    async def _object_rows(self, session, where: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        result = await session.execute(text(f"""
            SELECT c.oid, n.nspname AS schema, c.relname AS name,
                   {_OBJECT_DESCRIPTION_SQL} AS description,
                    o.id AS object_id, o.data_type, o.data_type_source, COALESCE(o.visibility, 'normal') AS visibility,
                   CASE WHEN NULLIF(obj_description(c.oid, 'pg_class'), '') IS NOT NULL
                        THEN 'table_comment' ELSE o.description_source END AS description_source,
                   {_OBJECT_GROUP_SQL} AS resource_group,
                   (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = c.oid
                    AND a.attnum > 0 AND NOT a.attisdropped) AS properties_count,
                   storage.object_count
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            {_OBJECT_METADATA_JOIN}
            CROSS JOIN LATERAL (
                WITH RECURSIVE descendants(relid) AS (
                    SELECT c.oid UNION
                    SELECT i.inhrelid FROM pg_inherits i
                    JOIN descendants d ON i.inhparent = d.relid
                )
                SELECT COALESCE(sum(CASE WHEN physical.relkind = 'p' THEN 0
                    ELSE GREATEST(0, pg_stat_get_live_tuples(d.relid)) END), 0) AS object_count
                FROM descendants d JOIN pg_class physical ON physical.oid = d.relid
            ) storage
            WHERE {where}
            ORDER BY CASE WHEN COALESCE(o.visibility, 'normal') = 'prominent' THEN 0 ELSE 1 END,
                n.nspname, c.relname LIMIT :limit OFFSET :offset
        """), params)
        return [dict(row) for row in result.mappings()]

    async def object_types(self, schema: str | None = None, search: str = "", group: str = "",
                           limit: int = 100, offset: int = 0, ids: str | None = None, *,
                           include_hidden: bool = False) -> dict[str, Any]:
        if not 1 <= limit <= 500 or not 0 <= offset <= 1_000_000:
            raise HTTPException(400, "Pagination is outside supported bounds")
        if group and group not in _OBJECT_GROUPS:
            raise HTTPException(400, "Unknown object type group")
        if len(search) > 1000:
            raise HTTPException(400, "Search is too long")
        params: dict[str, Any] = {"search": search.strip(), "limit": limit, "offset": offset}
        where = _OBJECT_TABLE_SQL + f""" AND position(lower(:search) in lower(
            n.nspname || '.' || c.relname || ' ' || {_OBJECT_DESCRIPTION_SQL})) > 0"""
        if not include_hidden:
            where += " AND COALESCE(o.visibility, 'normal') <> 'hidden'"
        if schema:
            params["schema"] = _object_schema(schema)
        if ids:
            identifiers = list(dict.fromkeys(ids.split(",")))
            if len(identifiers) > 200:
                raise HTTPException(400, "At most 200 object type identifiers are supported")
            for identifier in identifiers:
                parts = identifier.split(".")
                if len(parts) != 2 or not parts[0].startswith("ts_"):
                    raise HTTPException(400, "Invalid object type identifier")
                _object_schema(parts[0])
                quote_identifier(parts[1])
            params["ids"] = identifiers
            where += " AND n.nspname || '.' || c.relname = ANY(CAST(:ids AS text[]))"
        facet_where = where
        if schema:
            where += " AND n.nspname = :schema"
        async with self.session() as session:
            await self._require_ontology(session)
            counts = await session.execute(text(f"""
                SELECT n.nspname AS schema, {_OBJECT_GROUP_SQL} AS resource_group, count(*) AS count
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                {_OBJECT_METADATA_JOIN}
                WHERE {facet_where} GROUP BY n.nspname, {_OBJECT_GROUP_SQL} ORDER BY n.nspname
            """), params)
            schema_counts: dict[str, int] = {}
            group_counts = dict.fromkeys(_OBJECT_GROUPS, 0)
            group_schema_counts: dict[str, dict[str, int]] = {key: {} for key in _OBJECT_GROUPS}
            for row in counts.mappings():
                group_schema_counts[row["resource_group"]][row["schema"]] = int(row["count"])
                if schema and row["schema"] != params["schema"]:
                    continue
                schema_counts[row["schema"]] = schema_counts.get(row["schema"], 0) + int(row["count"])
                group_counts[row["resource_group"]] += int(row["count"])
            total = group_counts[group] if group else sum(schema_counts.values())
            if group:
                where += f" AND ({_OBJECT_GROUP_SQL}) = :group"
                params["group"] = group
            rows = await self._object_rows(session, where, params) if offset < total else []
        return {"items": [_object_summary(row) for row in rows], "total": total,
                "limit": limit, "offset": offset, "schemaCounts": schema_counts,
                "groupCounts": group_counts, "groupSchemaCounts": group_schema_counts}

    async def preview(self, layer: str, table: str, *, include_hidden: bool = False) -> dict[str, Any]:
        schema = _object_schema(layer)
        quote_identifier(table)
        async with self.session() as session:
            await self._require_ontology(session)
            visible = "" if include_hidden else " AND COALESCE(o.visibility, 'normal') <> 'hidden'"
            rows = await self._object_rows(session, _OBJECT_TABLE_SQL + visible +
                " AND n.nspname = :schema AND c.relname = :table",
                {"schema": schema, "table": table, "limit": 1, "offset": 0})
            if not rows:
                raise HTTPException(404, "Table does not exist in this schema")
            row = rows[0]
            result = await session.execute(text("""
                SELECT con.conname AS name, sn.nspname AS source_schema, sc.relname AS source_table,
                       tn.nspname AS target_schema, tc.relname AS target_table,
                       ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(attnum, position)
                             JOIN pg_attribute a ON a.attrelid = sc.oid AND a.attnum = k.attnum
                             ORDER BY k.position) AS columns,
                        so.data_type AS source_data_type, target_object.data_type AS target_data_type,
                        COALESCE(so.visibility, 'normal') AS source_visibility, COALESCE(target_object.visibility, 'normal') AS target_visibility,
                       ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(attnum, position)
                             JOIN pg_attribute a ON a.attrelid = tc.oid AND a.attnum = k.attnum
                             ORDER BY k.position) AS referenced_columns
                FROM pg_constraint con
                JOIN pg_class sc ON sc.oid = con.conrelid
                JOIN pg_namespace sn ON sn.oid = sc.relnamespace
                JOIN pg_class tc ON tc.oid = con.confrelid
                JOIN pg_namespace tn ON tn.oid = tc.relnamespace
                LEFT JOIN public.ontology_objects so ON so.schema_name = sn.nspname
                    AND so.table_name = sc.relname AND so.is_active
                LEFT JOIN public.ontology_objects target_object ON target_object.schema_name = tn.nspname
                    AND target_object.table_name = tc.relname AND target_object.is_active
                WHERE con.contype = 'f' AND con.conparentid = 0
                  AND (sc.oid = CAST(:oid AS oid) OR tc.oid = CAST(:oid AS oid))
                  AND sn.nspname ~ '^ts_[a-z0-9_]+$' AND sn.nspname <> 'ts_meta'
                  AND sn.nspname <> 'ts_meta_hist' AND sn.nspname <> :restricted
                  AND tn.nspname ~ '^ts_[a-z0-9_]+$' AND tn.nspname <> 'ts_meta'
                  AND tn.nspname <> 'ts_meta_hist' AND tn.nspname <> :restricted
                  AND sc.relname ~ '^[a-z_][a-z0-9_]{0,62}$'
                  AND tc.relname ~ '^[a-z_][a-z0-9_]{0,62}$'
                  AND sc.relkind IN ('r','p') AND tc.relkind IN ('r','p')
                  AND NOT sc.relispartition AND NOT tc.relispartition
                  AND NOT EXISTS (SELECT 1 FROM pg_inherits child WHERE child.inhrelid = sc.oid)
                  AND NOT EXISTS (SELECT 1 FROM pg_inherits child WHERE child.inhrelid = tc.oid)
                ORDER BY sn.nspname, sc.relname, con.conname, con.oid
            """), {"oid": row["oid"], "restricted": PROTECTED_SYNTHETIC_SCHEMA})
            foreign_keys = [dict(item) for item in result.mappings()]
            try:
                logical = await read_object_relations(session, schema, table)
            except ObjectRelationsNotInitialized as exc:
                raise HTTPException(503, str(exc)) from exc
            if row.get("object_id") is not None:
                result = await session.execute(text("""
                    SELECT p.name, p.sql_type AS type, p.comment, p.is_nullable AS nullable,
                           p.is_primary_key AS primary, p.primary_key_position AS "primaryPosition"
                    FROM public.ontology_object_properties p
                    JOIN pg_attribute a ON a.attrelid = CAST(:oid AS oid) AND a.attname = p.name
                        AND a.attnum > 0 AND NOT a.attisdropped
                    LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
                    LEFT JOIN pg_index i ON i.indrelid = a.attrelid AND i.indisprimary
                    WHERE p.object_id = :object_id
                      AND p.sql_type = format_type(a.atttypid, a.atttypmod)
                      AND p.ordinal_position = a.attnum AND p.is_nullable = NOT a.attnotnull
                      AND p.identity_kind = a.attidentity::text AND p.generated_kind = a.attgenerated::text
                      AND p.comment = COALESCE(col_description(a.attrelid, a.attnum), '')
                      AND p.default_expression IS NOT DISTINCT FROM
                          CASE WHEN a.attgenerated = '' THEN pg_get_expr(d.adbin, d.adrelid) END
                      AND p.generation_expression IS NOT DISTINCT FROM
                          CASE WHEN a.attgenerated <> '' THEN pg_get_expr(d.adbin, d.adrelid) END
                      AND p.is_primary_key = COALESCE(array_position(i.indkey::smallint[], a.attnum) < i.indnkeyatts, false)
                      AND p.primary_key_position IS NOT DISTINCT FROM
                          CASE WHEN array_position(i.indkey::smallint[], a.attnum) < i.indnkeyatts
                               THEN array_position(i.indkey::smallint[], a.attnum) + 1 END
                    ORDER BY p.ordinal_position
                """), {"object_id": row["object_id"], "oid": row["oid"]})
                columns = [dict(item) for item in result.mappings()]
                if len(columns) != row["properties_count"]:
                    raise HTTPException(409, "Object properties differ from the table structure; synchronize its Ontology metadata")
            else:
                columns = None
        if columns is None:
            columns = await self.columns(schema, table) if row["properties_count"] else []
        linked: dict[str, dict[str, Any]] = {}
        for collection, relations in (("constraints", foreign_keys),
                ("relationships", merge_object_relations(foreign_keys, logical))):
            for relation in relations:
                if any(not relation[f"{side}_schema"].startswith("ts_") or
                       relation[f"{side}_schema"] in {"ts_meta", "ts_meta_hist", PROTECTED_SYNTHETIC_SCHEMA}
                       for side in ("source", "target")):
                    continue
                for direction, side, peer in (("outgoing", "source", "target"), ("incoming", "target", "source")):
                    if (relation[f"{side}_schema"], relation[f"{side}_table"]) != (schema, table):
                        continue
                    peer_schema, peer_table = relation[f"{peer}_schema"], relation[f"{peer}_table"]
                    if not include_hidden and relation[f"{peer}_visibility"] == "hidden":
                        continue
                    identifier = f"{peer_schema}.{peer_table}"
                    item = linked.setdefault(identifier, {"id": identifier, "schema": peer_schema,
                        "table": peer_table, "name": peer_table, "displayName": peer_table,
                        "dataType": relation.get(f"{peer}_data_type") or "",
                        "visibility": relation[f"{peer}_visibility"],
                        "direction": direction, "constraints": [], "relationships": []})
                    if item["direction"] != direction:
                        item["direction"] = "both"
                    entry = {"name": relation["name"], "direction": direction,
                        "sourceSchema": relation["source_schema"], "sourceTable": relation["source_table"],
                        "targetSchema": relation["target_schema"], "targetTable": relation["target_table"],
                        "columns": list(relation["columns"]), "referencedColumns": list(relation["referenced_columns"])}
                    if collection == "relationships":
                        entry.update(sourceKind=relation["source_kind"], relationshipKind=relation["relationship_kind"],
                            cardinality=relation["cardinality"], description=relation["description"],
                            provenance=relation["provenance"])
                    item[collection].append(entry)
        primary = sorted((column for column in columns if column["primary"]), key=lambda column: column["primaryPosition"])
        return {**_object_summary(row), "primaryKey": [column["name"] for column in primary],
                "properties": [{"name": column["name"], "displayName": column["name"],
                    "type": column["type"], "nullable": column["nullable"],
                    "primaryKey": column["primary"], "comment": column["comment"] or ""} for column in columns],
                "linkedObjectTypes": [linked[key] for key in sorted(linked)]}

    async def get_table(self, layer: str, table: str) -> dict[str, Any]:
        quote_identifier(table)
        tables = await self.tables(layer)
        found = next((item for item in tables if item["name"] == table), None)
        if found is None:
            raise HTTPException(404, "Table does not exist in this schema")
        return found

    async def transfer_endpoints(self, source: tuple[str, str], target: tuple[str, str]) -> dict[tuple[str, str], bool]:
        """Return eligible visible tables and whether they have a valid primary key."""
        async with self.session() as session:
            await session.execute(text("SET LOCAL statement_timeout = '2s'"))
            await self._require_ontology(session)
            result = await session.execute(text(f"""
                SELECT n.nspname AS schema_name, c.relname AS table_name,
                       EXISTS (SELECT 1 FROM pg_index i WHERE i.indrelid = c.oid
                               AND i.indisprimary AND i.indisvalid AND i.indisready) AS has_primary_key
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                {_OBJECT_METADATA_JOIN}
                WHERE {_TRANSFER_ENDPOINT_WHERE}
            """), {"source_schema": source[0], "source_table": source[1],
                    "target_schema": target[0], "target_table": target[1]})
            return {(row["schema_name"], row["table_name"]): row["has_primary_key"] for row in result.mappings()}

    async def transfer_endpoint_columns(
        self, source: tuple[str, str], target: tuple[str, str],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Read the metadata for both visible endpoints in one catalog snapshot."""
        async with self.session() as session:
            await session.execute(text("SET LOCAL statement_timeout = '2s'"))
            return await self.transfer_endpoint_columns_in_session(session, source, target)

    async def transfer_endpoint_columns_in_session(
        self, session, source: tuple[str, str], target: tuple[str, str],
    ) -> dict[tuple[str, str], dict[str, Any]]:
        await self._require_ontology(session)
        result = await session.execute(text(f"""
            WITH endpoints AS (
                SELECT n.nspname AS schema_name, c.relname AS table_name, c.oid,
                       p.index_name, p.key_attnum, p.index_name IS NOT NULL AS usable_primary_key
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN LATERAL (
                    SELECT idx.relname AS index_name,
                           CASE WHEN i.indnkeyatts = 1 AND am.amname = 'btree' THEN i.indkey[0] END AS key_attnum
                    FROM pg_index i JOIN pg_class idx ON idx.oid = i.indexrelid
                    JOIN pg_am am ON am.oid = idx.relam
                    WHERE i.indrelid = c.oid AND i.indisprimary AND i.indisvalid AND i.indisready
                      AND i.indislive AND i.indimmediate
                ) p ON true
                {_OBJECT_METADATA_JOIN}
                WHERE {_TRANSFER_ENDPOINT_WHERE}
            )
            SELECT e.schema_name, e.table_name, e.usable_primary_key, e.index_name,
                   a.attnum = e.key_attnum AS single_btree_pk, a.attname AS name,
                   a.atttypid AS type_oid, a.atttypmod AS type_modifier,
                   format_type(a.atttypid, a.atttypmod) AS type,
                   a.attnotnull AS not_null, a.attgenerated::text AS generated, a.attidentity::text AS identity
            FROM endpoints e LEFT JOIN pg_attribute a
              ON a.attrelid = e.oid AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY e.schema_name, e.table_name, a.attnum
        """), {"source_schema": source[0], "source_table": source[1],
                "target_schema": target[0], "target_table": target[1]})
        endpoints: dict[tuple[str, str], dict[str, Any]] = {}
        for row in result.mappings():
            endpoint = endpoints.setdefault((row["schema_name"], row["table_name"]),
                                            {"has_usable_pk": row["usable_primary_key"],
                                             "index_name": row["index_name"], "columns": {}})
            if row["name"] is not None:
                endpoint["columns"][row["name"]] = row
        return endpoints

    async def evidence(self, layer: str, table: str, *, include_hidden: bool = False) -> dict[str, Any]:
        schema = _object_schema(layer)
        quote_identifier(table)
        is_financial = (schema, table) == _FINANCIAL_ODS
        filed = None
        observation = "safe_plan_unavailable" if is_financial else "metadata_only"
        async with self.session() as session:
            await session.execute(text("SET LOCAL statement_timeout = '2s'"))
            await self._require_ontology(session)
            target = (await session.execute(text(f"""
                SELECT attributes.column_count, attributes.primary_key_column_count,
                       attributes.not_null_column_count, attributes.filed_is_date
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                {_OBJECT_METADATA_JOIN}
                CROSS JOIN LATERAL (
                    SELECT count(*) AS column_count,
                           count(*) FILTER (WHERE a.attnotnull) AS not_null_column_count,
                           count(*) FILTER (WHERE array_position(i.indkey::smallint[], a.attnum) < i.indnkeyatts)
                               AS primary_key_column_count,
                           bool_or(a.attname = 'filed' AND a.atttypid = 'date'::regtype) AS filed_is_date
                    FROM pg_attribute a
                    LEFT JOIN pg_index i ON i.indrelid = c.oid AND i.indisprimary
                    WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                ) attributes
                WHERE {_OBJECT_TABLE_SQL} AND n.nspname = :schema AND c.relname = :table
                    AND (:include_hidden OR COALESCE(o.visibility, 'normal') <> 'hidden')
            """), {"schema": schema, "table": table, "include_hidden": include_hidden})).mappings().first()
            if target is None:
                raise HTTPException(404, "Evidence is not available for this table")
            if is_financial and target["filed_is_date"]:
                plan = (await session.execute(text("EXPLAIN (FORMAT JSON) " + _LATEST_FILED_SQL))).scalar_one()
                if _bounded_filed_plan(plan):
                    filed = (await session.execute(text(_LATEST_FILED_SQL))).scalar_one_or_none()
                    observation = "observed" if filed is not None else "no_filed_date"
        return {
            "observedAt": datetime.now(timezone.utc).isoformat(),
            "latestFiledDate": filed.isoformat() if filed is not None else None,
            "observation": observation,
            "quality": {
                "source": "postgres_catalog",
                "columnCount": target["column_count"],
                "primaryKeyColumnCount": target["primary_key_column_count"],
                "notNullColumnCount": target["not_null_column_count"],
            },
            "history": {"status": "unavailable"},
            "lineage": {
                "status": "declared",
                "mongoCollection": "financial_fact_sec_edgar",
                "rdsTable": "ts_rds.rds_financial_fact",
                "odsTable": "ts_ods.ods_financial_fact",
            } if is_financial else {"status": "unavailable"},
        }

    async def columns(self, layer: str, table: str) -> list[dict[str, Any]]:
        schema = resolve_schema(layer)
        quote_identifier(table)
        async with self.session() as session:
            result = await session.execute(text("""
                SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,
                       col_description(c.oid, a.attnum) AS comment,
                       a.attnum AS "ordinalPosition",
                       NOT a.attnotnull AS nullable,
                       COALESCE(array_position(i.indkey::smallint[], a.attnum) < i.indnkeyatts, false) AS primary,
                       CASE WHEN array_position(i.indkey::smallint[], a.attnum) < i.indnkeyatts
                            THEN array_position(i.indkey::smallint[], a.attnum) + 1 END AS "primaryPosition"
                FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_index i ON i.indrelid = c.oid AND i.indisprimary
                WHERE n.nspname = :schema AND c.relname = :table
                  AND c.relkind IN ('r','p') AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum
            """), {"schema": schema, "table": table})
            columns = [dict(row) for row in result.mappings()]
        if not columns:
            raise HTTPException(404, "Table does not exist in this schema")
        return columns

    async def partitions(self, layer: str, table: str) -> list[dict[str, Any]]:
        schema = resolve_schema(layer)
        await self.columns(schema, table)
        async with self.session() as session:
            result = await session.execute(text("""
                WITH RECURSIVE descendants(relid) AS (
                    SELECT to_regclass(:qualified)
                    UNION
                    SELECT i.inhrelid FROM pg_inherits i
                    JOIN descendants d ON i.inhparent = d.relid
                )
                SELECT c.relname AS name, n.nspname AS schema,
                       pg_get_expr(c.relpartbound,c.oid) AS bounds
                FROM descendants t
                JOIN pg_class c ON c.oid = t.relid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE t.relid <> to_regclass(:qualified)
                  AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhparent = t.relid)
                ORDER BY c.relname
            """), {"qualified": f'{quote_identifier(schema)}.{quote_identifier(table)}'})
            return [dict(row) for row in result.mappings()]

    async def data(self, layer: str, table: str, payload: dict[str, Any] | None = None,
                   scope: dict[str, Any] | None = None, *, preview_rows: bool = False) -> dict[str, Any]:
        started = perf_counter()
        payload = payload or {}
        schema = resolve_schema(layer)
        if (schema, table) == (INDEX_SANDBOX_SCHEMA, INDEX_SANDBOX_TABLE):
            raise HTTPException(403, "Use the dedicated index-checked preview for this synthetic table")
        columns = await self.columns(schema, table)
        primary_columns = sorted((column for column in columns if column["primary"]),
                                 key=lambda column: column["primaryPosition"])
        names = {column["name"] for column in columns}
        qualified = f'{quote_identifier(schema)}.{quote_identifier(table)}'
        target = qualified
        # Published partitions are an additional restriction, never replaceable by callers.
        selected_partitions = {item for item in (payload.get("partition"), (scope or {}).get("partition")) if item}
        if len(selected_partitions) > 1:
            raise HTTPException(400, "Partition is outside the published data range")
        if selected_partitions:
            selected = selected_partitions.pop()
            parts = await self.partitions(schema, table)
            part = next((item for item in parts if item["name"] == selected), None)
            if part is None:
                raise HTTPException(400, "Partition does not belong to this table")
            target = f'{quote_identifier(part["schema"])}.{quote_identifier(part["name"])}'
        try:
            limit = int(payload.get("limit", 50))
            offset = int(payload.get("offset", 0))
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, "Invalid pagination") from exc
        max_limit = PREVIEW_ROWS_MAX_LIMIT if preview_rows else 500
        max_offset = PREVIEW_ROWS_MAX_OFFSET if preview_rows else 1_000_000
        if not 1 <= limit <= max_limit or not 0 <= offset <= max_offset:
            raise HTTPException(400, "Pagination is outside supported bounds")
        params: dict[str, Any] = {"limit": limit + int(preview_rows), "offset": offset}
        where: list[str] = []
        for restriction in (scope or {}, payload):
            if restriction.get("search"):
                key = f"p{len(params)}"
                params[key] = f'%{restriction["search"]}%'
                where.append(f"CAST(row_to_json(t) AS text) ILIKE :{key}")
            expression = compile_filters(restriction.get("filters", []), names, qualified, params)
            if expression:
                where.append(expression)
        for field, selected in (("data_type", (scope or {}).get("filterType")),
                                ("data_source", (scope or {}).get("filterSource"))):
            if selected:
                actual = field if field in names else "source" if field == "data_source" and "source" in names else None
                if actual is None:
                    raise HTTPException(400, f"Table has no {field} column for this data range")
                key = f"p{len(params)}"
                params[key] = json.dumps(selected)
                where.append(f'CAST(t.{quote_identifier(actual)} AS text) IN (SELECT jsonb_array_elements_text(CAST(:{key} AS jsonb)))')
        sorts = payload.get("sorts") or (scope or {}).get("sorts") or []
        if not isinstance(sorts, list) or len(sorts) > 20:
            raise HTTPException(400, "Invalid sorts")
        order = []
        sorted_fields = set()
        for sort in sorts:
            if (not isinstance(sort, dict) or not isinstance(sort.get("field"), str)
                    or sort["field"] not in names or sort.get("direction") not in {"asc", "desc"}):
                raise HTTPException(400, "Invalid sort field or direction")
            order.append(f't.{quote_identifier(sort["field"])} {sort["direction"]}')
            sorted_fields.add(sort["field"])
        primary_key = [column["name"] for column in primary_columns]
        if preview_rows:
            order.extend(f't.{quote_identifier(field)} asc' for field in primary_key if field not in sorted_fields)
        predicate = " WHERE " + " AND ".join(f"({item})" for item in where) if where else ""
        ordering = " ORDER BY " + ", ".join(order) if order else ""
        async with self.session() as session:
            if not preview_rows:
                total = (await session.execute(text(f"SELECT count(*) FROM (SELECT 1 FROM {target} t{predicate} LIMIT 10001) flow_count"), params)).scalar_one() if where else 0
                estimated = not where or total > 10000
                if estimated:
                    plan = (await session.execute(text(f"EXPLAIN (FORMAT JSON) SELECT 1 FROM {target} t{predicate}"), params)).scalar_one()
                    if isinstance(plan, str):
                        plan = json.loads(plan)
                    total = max(total, int(plan[0]["Plan"]["Plan Rows"]))
            result = await session.execute(text(f"SELECT t.* FROM {target} t{predicate}{ordering} LIMIT :limit OFFSET :offset"), params)
            rows = [dict(row) for row in result.mappings()]
        if preview_rows:
            has_more = len(rows) > limit
            rows = rows[:limit]
            page = {"hasMore": has_more, "canPageFurther": has_more and offset + limit <= max_offset,
                    "stableOrder": bool(primary_key)}
        else:
            if rows:
                total = max(total, offset + len(rows))
            elif offset == 0:
                total, estimated = 0, False
            page = {"total": total, "totalEstimated": estimated}
        return {"columns": [column["name"] for column in columns], "rows": rows,
                "rowCount": len(rows), **page,
                "primaryKey": primary_key,
                "columnTypes": {column["name"]: column["type"] for column in columns},
                "elapsed": round(perf_counter() - started, 4)}

    async def query(self, sql: str) -> dict[str, Any]:
        sql = sql.strip().removesuffix(";").strip()
        if not re.match(r"^(SELECT|WITH)\b", sql, re.IGNORECASE) or ";" in sql:
            raise HTTPException(400, "Only a single read-only SELECT query is supported")
        started = perf_counter()
        async with self.session() as session:
            # Raw SQL is not a relation sandbox: views and database functions can hide references.
            # Leave the ordinary environment unchanged unless this isolated fixture exists or history is protected.
            fixture, marked_history_column = (await session.execute(text("""
                SELECT pg_catalog.to_regnamespace(:fixture) IS NOT NULL,
                       EXISTS (
                           SELECT 1 FROM pg_catalog.pg_attribute
                           WHERE attrelid = pg_catalog.to_regclass('public.ontology_cdc_sandbox_versions')
                             AND attname = 'marking_level' AND attnum > 0 AND NOT attisdropped
                       )
            """), {"fixture": PROTECTED_SYNTHETIC_SCHEMA})).one()
            if fixture or (marked_history_column and await session.scalar(text("""
                SELECT EXISTS (SELECT 1 FROM public.ontology_cdc_sandbox_versions WHERE marking_level IS NOT NULL)
            """))):
                raise HTTPException(403, "Free-form SQL is unavailable in the protected synthetic workspace")
            result = await session.execute(text(f"SELECT * FROM ({sql}) AS flow_query LIMIT 500".replace(":", r"\:")))
            columns = list(result.keys())
            rows = [dict(row) for row in result.mappings()]
        return {"columns": columns, "rows": rows, "rowCount": len(rows),
                "elapsed": round(perf_counter() - started, 4)}


def compile_filters(rules: list[dict[str, Any]], names: set[str], qualified: str,
                    params: dict[str, Any]) -> str:
    """Compile the page's nested filter rows, with values always bound as parameters."""
    if not isinstance(rules, list) or len(rules) > 100:
        raise HTTPException(400, "Invalid filters")
    stack: list[list[tuple[str, str]]] = [[]]
    enabled = [True]
    for rule in rules:
        if not isinstance(rule, dict):
            raise HTTPException(400, "Invalid filter row")
        join = str(rule.get("join", "and")).upper()
        if join not in {"AND", "OR"}:
            raise HTTPException(400, "Invalid filter conjunction")
        row_id = str(rule.get("id", ""))
        if row_id.startswith("group-open-"):
            stack.append([])
            enabled.append(enabled[-1] and rule.get("active", True) is not False)
            continue
        if row_id.startswith("group-close-"):
            if len(stack) == 1:
                raise HTTPException(400, "Unbalanced filter group")
            children = stack.pop()
            enabled.pop()
            if children:
                stack[-1].append((join, "(" + _join_filters(children) + ")"))
            continue
        if not enabled[-1] or rule.get("active", True) is False:
            continue
        field = rule.get("field")
        if not isinstance(field, str) or field not in names:
            raise HTTPException(400, "Unknown filter field")
        col = f't.{quote_identifier(field)}'
        op = re.sub(r"\s+", " ", str(rule.get("operator", "="))).strip()
        raw = rule.get("value", "")
        unary = {"是 null": f"{col} IS NULL", "不是 null": f"{col} IS NOT NULL",
                 "是空的": f"CAST({col} AS text) = ''", "不是空的": f"CAST({col} AS text) <> ''"}
        if op in unary:
            stack[-1].append((join, unary[op]))
            continue
        if raw == "" or raw is None:
            continue
        def bind(value: Any, typed: bool = True, field: str = field) -> str:
            key = f"p{len(params)}"
            params[key] = json.dumps({field: value}, ensure_ascii=False) if typed else value
            return f'(jsonb_populate_record(NULL::{qualified}, CAST(:{key} AS jsonb))).{quote_identifier(field)}' if typed else f":{key}"
        if op in {"=", "!=", "<", "<=", ">", ">="}:
            expr = f"{col} {op} {bind(raw)}"
        elif op in {"在列表", "不在列表", "介于", "不介于"}:
            values = raw if isinstance(raw, list) else [value.strip() for value in str(raw).split(",")]
            if not values:
                raise HTTPException(400, "List filters require at least one value")
            if op in {"介于", "不介于"}:
                if len(values) != 2:
                    raise HTTPException(400, "Between requires two values")
                expr = f"{col} {'NOT BETWEEN' if op == '不介于' else 'BETWEEN'} {bind(values[0])} AND {bind(values[1])}"
            else:
                expr = f"{col} {'NOT IN' if op == '不在列表' else 'IN'} ({', '.join(bind(value) for value in values)})"
        elif op in {"包含", "包含（不区分大小写）", "不包含", "不包含（不区分大小写）", "开始以", "不是开始于", "ends with", "不是结束于"}:
            value = str(raw).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            value = value + "%" if op in {"开始以", "不是开始于"} else "%" + value if op in {"ends with", "不是结束于"} else "%" + value + "%"
            negative = "NOT " if op.startswith(("不包含", "不是")) else ""
            operator = "ILIKE" if "不区分大小写" in op else "LIKE"
            expr = f"CAST({col} AS text) {negative}{operator} {bind(value, False)}"
        else:
            raise HTTPException(400, "Unsupported filter operator")
        stack[-1].append((join, expr))
    if len(stack) != 1:
        raise HTTPException(400, "Unbalanced filter group")
    return _join_filters(stack[0])


def _join_filters(items: list[tuple[str, str]]) -> str:
    return " ".join((f"{items[index - 1][0]} " if index else "") + f"({expr})" for index, (_, expr) in enumerate(items))


_service: EtlLayers | None = None


def get_etl_layers() -> EtlLayers:
    global _service
    if _service is None:
        _service = EtlLayers()
    return _service
