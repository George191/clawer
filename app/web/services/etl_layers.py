"""PostgreSQL catalog and bounded data access for the Flow workspace."""

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
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

LAYERS = ("rds", "ods", "task", "dwd", "dws", "dim", "ads")
_OBJECT_GROUPS = ("ingestion", "processing", "serving", "other")
_OBJECT_GROUP_SQL = """CASE regexp_replace(n.nspname, '_hist$', '')
    WHEN 'ts_rds' THEN 'ingestion' WHEN 'ts_ods' THEN 'ingestion'
    WHEN 'ts_task' THEN 'processing' WHEN 'ts_dim' THEN 'processing'
    WHEN 'ts_dwd' THEN 'processing' WHEN 'ts_dws' THEN 'processing'
    WHEN 'ts_ads' THEN 'serving' ELSE 'other' END"""
_OBJECT_TABLE_SQL = """n.nspname ~ '^ts_[a-z0-9_]+$' AND n.nspname <> 'ts_meta'
    AND n.nspname <> 'ts_meta_hist'
    AND c.relname ~ '^[a-z_][a-z0-9_]{0,62}$' AND c.relkind IN ('r','p')
    AND NOT c.relispartition
    AND NOT EXISTS (SELECT 1 FROM pg_inherits child WHERE child.inhrelid = c.oid)"""


def quote_identifier(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name):
        raise HTTPException(400, "Invalid PostgreSQL identifier")
    return f'"{name}"'


def resolve_schema(layer: str, table_role: str = "current") -> str:
    if table_role not in {"current", "history"}:
        raise HTTPException(400, "Unsupported table role")
    schema = layer if layer.startswith("ts_") else f"ts_{layer}"
    quote_identifier(schema)
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
        "objectCount": int(row["object_count"]), "objectCountEstimated": True,
        "propertiesCount": int(row["properties_count"]), "status": "available",
        "titleProperty": None, "visibility": "not_configured",
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
                WHERE n.nspname LIKE 'ts\_%' ESCAPE '\'
                GROUP BY n.oid, n.nspname ORDER BY n.nspname
            """))
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
            result = await session.execute(text("""
                SELECT c.relname AS name, c.relkind = 'p' OR storage.has_children AS partitioned,
                       (SELECT CASE WHEN count(DISTINCT a.data_type) = 1
                                    THEN min(a.data_type) END
                        FROM public.ai_collect_template_alias a
                        WHERE regexp_replace(regexp_replace(c.relname,
                                  '^(rds|ods|raw|dwd|dws|dim|ads|stg|etl|tmp|dw|dm|task)_', ''),
                                  '_p[0-9]{1,3}$', '')
                              IN (a.collection_name, a.template_name)) AS data_type,
                       obj_description(c.oid, 'pg_class') AS description,
                       storage.rows, storage.size
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
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

    async def _object_rows(self, session, where: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        alias_exists = (await session.execute(text(
            "SELECT to_regclass('public.ai_collect_template_alias') IS NOT NULL"
        ))).scalar_one()
        data_type = """(SELECT CASE WHEN count(DISTINCT a.data_type) = 1
                                THEN min(a.data_type) END
            FROM public.ai_collect_template_alias a
            WHERE regexp_replace(regexp_replace(c.relname,
                  '^(rds|ods|raw|dwd|dws|dim|ads|stg|etl|tmp|dw|dm|task)_', ''),
                  '_p[0-9]{1,3}$', '') IN (a.collection_name, a.template_name))""" if alias_exists else "NULL::text"
        result = await session.execute(text(f"""
            SELECT c.oid, n.nspname AS schema, c.relname AS name,
                   obj_description(c.oid, 'pg_class') AS description,
                   {data_type} AS data_type,
                   (SELECT count(*) FROM pg_attribute a WHERE a.attrelid = c.oid
                    AND a.attnum > 0 AND NOT a.attisdropped) AS properties_count,
                   storage.object_count
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
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
            ORDER BY n.nspname, c.relname LIMIT :limit OFFSET :offset
        """), params)
        return [dict(row) for row in result.mappings()]

    async def object_types(self, schema: str | None = None, search: str = "", group: str = "",
                           limit: int = 100, offset: int = 0, ids: str | None = None) -> dict[str, Any]:
        if not 1 <= limit <= 500 or not 0 <= offset <= 1_000_000:
            raise HTTPException(400, "Pagination is outside supported bounds")
        if group and group not in _OBJECT_GROUPS:
            raise HTTPException(400, "Unknown object type group")
        if len(search) > 1000:
            raise HTTPException(400, "Search is too long")
        params: dict[str, Any] = {"search": search.strip(), "limit": limit, "offset": offset}
        where = _OBJECT_TABLE_SQL + """ AND position(lower(:search) in lower(
            n.nspname || '.' || c.relname || ' ' || COALESCE(obj_description(c.oid, 'pg_class'), ''))) > 0"""
        if schema:
            params["schema"] = _object_schema(schema)
            where += " AND n.nspname = :schema"
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
        async with self.session() as session:
            counts = await session.execute(text(f"""
                SELECT n.nspname AS schema, {_OBJECT_GROUP_SQL} AS resource_group, count(*) AS count
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE {where} GROUP BY n.nspname ORDER BY n.nspname
            """), params)
            schema_counts: dict[str, int] = {}
            group_counts = dict.fromkeys(_OBJECT_GROUPS, 0)
            for row in counts.mappings():
                schema_counts[row["schema"]] = int(row["count"])
                group_counts[row["resource_group"]] += int(row["count"])
            total = group_counts[group] if group else sum(schema_counts.values())
            if group:
                where += f" AND ({_OBJECT_GROUP_SQL}) = :group"
                params["group"] = group
            rows = await self._object_rows(session, where, params) if offset < total else []
        return {"items": [_object_summary(row) for row in rows], "total": total,
                "limit": limit, "offset": offset, "schemaCounts": schema_counts,
                "groupCounts": group_counts}

    async def preview(self, layer: str, table: str) -> dict[str, Any]:
        schema = _object_schema(layer)
        quote_identifier(table)
        async with self.session() as session:
            rows = await self._object_rows(session, _OBJECT_TABLE_SQL +
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
                       ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(attnum, position)
                             JOIN pg_attribute a ON a.attrelid = tc.oid AND a.attnum = k.attnum
                             ORDER BY k.position) AS referenced_columns
                FROM pg_constraint con
                JOIN pg_class sc ON sc.oid = con.conrelid
                JOIN pg_namespace sn ON sn.oid = sc.relnamespace
                JOIN pg_class tc ON tc.oid = con.confrelid
                JOIN pg_namespace tn ON tn.oid = tc.relnamespace
                WHERE con.contype = 'f' AND con.conparentid = 0
                  AND (sc.oid = CAST(:oid AS oid) OR tc.oid = CAST(:oid AS oid))
                  AND sn.nspname ~ '^ts_[a-z0-9_]+$' AND sn.nspname <> 'ts_meta'
                  AND sn.nspname <> 'ts_meta_hist'
                  AND tn.nspname ~ '^ts_[a-z0-9_]+$' AND tn.nspname <> 'ts_meta'
                  AND tn.nspname <> 'ts_meta_hist'
                  AND sc.relname ~ '^[a-z_][a-z0-9_]{0,62}$'
                  AND tc.relname ~ '^[a-z_][a-z0-9_]{0,62}$'
                  AND sc.relkind IN ('r','p') AND tc.relkind IN ('r','p')
                  AND NOT sc.relispartition AND NOT tc.relispartition
                  AND NOT EXISTS (SELECT 1 FROM pg_inherits child WHERE child.inhrelid = sc.oid)
                  AND NOT EXISTS (SELECT 1 FROM pg_inherits child WHERE child.inhrelid = tc.oid)
                ORDER BY sn.nspname, sc.relname, con.conname, con.oid
            """), {"oid": row["oid"]})
            foreign_keys = [dict(item) for item in result.mappings()]
            try:
                logical = await read_object_relations(session, schema, table)
            except ObjectRelationsNotInitialized as exc:
                raise HTTPException(503, str(exc)) from exc
        columns = await self.columns(schema, table) if row["properties_count"] else []
        linked: dict[str, dict[str, Any]] = {}
        for collection, relations in (("constraints", foreign_keys),
                ("relationships", merge_object_relations(foreign_keys, logical))):
            for relation in relations:
                if any(not relation[f"{side}_schema"].startswith("ts_") or
                       relation[f"{side}_schema"] in {"ts_meta", "ts_meta_hist"}
                       for side in ("source", "target")):
                    continue
                for direction, side, peer in (("outgoing", "source", "target"), ("incoming", "target", "source")):
                    if (relation[f"{side}_schema"], relation[f"{side}_table"]) != (schema, table):
                        continue
                    peer_schema, peer_table = relation[f"{peer}_schema"], relation[f"{peer}_table"]
                    identifier = f"{peer_schema}.{peer_table}"
                    item = linked.setdefault(identifier, {"id": identifier, "schema": peer_schema,
                        "table": peer_table, "name": peer_table, "displayName": peer_table,
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

    async def columns(self, layer: str, table: str) -> list[dict[str, Any]]:
        schema = resolve_schema(layer)
        quote_identifier(table)
        async with self.session() as session:
            result = await session.execute(text("""
                SELECT a.attname AS name, format_type(a.atttypid, a.atttypmod) AS type,
                       col_description(c.oid, a.attnum) AS comment,
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
                   scope: dict[str, Any] | None = None) -> dict[str, Any]:
        started = perf_counter()
        payload = payload or {}
        schema = resolve_schema(layer)
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
        if not 1 <= limit <= 500 or not 0 <= offset <= 1_000_000:
            raise HTTPException(400, "Pagination is outside supported bounds")
        params: dict[str, Any] = {"limit": limit, "offset": offset}
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
        for sort in sorts:
            if (not isinstance(sort, dict) or not isinstance(sort.get("field"), str)
                    or sort["field"] not in names or sort.get("direction") not in {"asc", "desc"}):
                raise HTTPException(400, "Invalid sort field or direction")
            order.append(f't.{quote_identifier(sort["field"])} {sort["direction"]}')
        predicate = " WHERE " + " AND ".join(f"({item})" for item in where) if where else ""
        ordering = " ORDER BY " + ", ".join(order) if order else ""
        async with self.session() as session:
            # Opening a preview must not scan or globally sort every partition.
            total = (await session.execute(text(f"SELECT count(*) FROM (SELECT 1 FROM {target} t{predicate} LIMIT 10001) flow_count"), params)).scalar_one() if where else 0
            estimated = not where or total > 10000
            if estimated:
                plan = (await session.execute(text(f"EXPLAIN (FORMAT JSON) SELECT 1 FROM {target} t{predicate}"), params)).scalar_one()
                if isinstance(plan, str):
                    plan = json.loads(plan)
                total = max(total, int(plan[0]["Plan"]["Plan Rows"]))
            result = await session.execute(text(f"SELECT t.* FROM {target} t{predicate}{ordering} LIMIT :limit OFFSET :offset"), params)
            rows = [dict(row) for row in result.mappings()]
        if rows:
            total = max(total, offset + len(rows))
        elif offset == 0:
            total, estimated = 0, False
        return {"columns": [column["name"] for column in columns], "rows": rows,
                "rowCount": len(rows), "total": total, "totalEstimated": estimated,
                "primaryKey": [column["name"] for column in primary_columns],
                "columnTypes": {column["name"]: column["type"] for column in columns},
                "elapsed": round(perf_counter() - started, 4)}

    async def query(self, sql: str) -> dict[str, Any]:
        sql = sql.strip().removesuffix(";").strip()
        if not re.match(r"^(SELECT|WITH)\b", sql, re.IGNORECASE) or ";" in sql:
            raise HTTPException(400, "Only a single read-only SELECT query is supported")
        started = perf_counter()
        async with self.session() as session:
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
