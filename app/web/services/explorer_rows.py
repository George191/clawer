"""Keyed physical rows and declared row relationships in one read-only snapshot."""

import json
from decimal import Decimal
from math import isfinite

from fastapi import HTTPException
from sqlalchemy import text

from app.web.services.etl_layers import (
    INDEX_SANDBOX_SCHEMA, INDEX_SANDBOX_TABLE, ROW_VERSION_SQL, _LINEAGE_METADATA_JOIN,
    _OBJECT_TABLE_SQL, _object_schema, _required_indexed_preview_tables, _required_preview_family,
    allows_physical_delete, get_etl_layers, quote_identifier, require_ordinary_table, row_identity_sql,
)


def _name(schema, table):
    return f"{quote_identifier(schema)}.{quote_identifier(table)}"


async def _asset(session, schema, table):
    require_ordinary_table(_object_schema(schema), table)
    qualified = _name(schema, table)
    if (schema, table) == (INDEX_SANDBOX_SCHEMA, INDEX_SANDBOX_TABLE):
        raise HTTPException(404, "Table not available for record inspection")
    info = (await session.execute(text(f"""
        SELECT c.oid, c.relkind::text AS relkind, EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhparent=c.oid) AS has_children,
               c.relkind='r' AND pg_relation_size(c.oid) <= 1048576 AS small_heap,
               EXISTS (SELECT 1 FROM pg_partitioned_table p WHERE p.partrelid=c.oid AND p.partstrat='h') AS hash_partitioned
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace {_LINEAGE_METADATA_JOIN}
        WHERE {_OBJECT_TABLE_SQL} AND n.nspname=:schema AND c.relname=:table
          AND COALESCE(o.is_active,true) AND COALESCE(o.visibility,'normal') <> 'hidden'
          AND has_schema_privilege(n.oid,'USAGE') AND has_table_privilege(c.oid,'SELECT')
    """), {"schema": schema, "table": table})).mappings().first()
    if not info:
        raise HTTPException(404, "Table not available for record inspection")
    await session.execute(text(f"LOCK TABLE ONLY {qualified} IN ACCESS SHARE MODE NOWAIT"))
    if await _required_preview_family(session, schema, table, _required_indexed_preview_tables()):
        raise HTTPException(404, "Table not available for record inspection")
    if info["relkind"] == "r" and info["has_children"]:
        # Plain inheritance does not enforce the parent's primary key across
        # its children. Timescale maintains that invariant across its chunks.
        installed = await session.scalar(text("SELECT to_regclass('timescaledb_information.hypertables') IS NOT NULL"))
        registered = installed and await session.scalar(text("""SELECT EXISTS (
            SELECT 1 FROM timescaledb_information.hypertables WHERE hypertable_schema=:schema AND hypertable_name=:table
        )"""), {"schema": schema, "table": table})
        if not registered:
            raise HTTPException(404, "Table not available for record inspection")
    columns = (await session.execute(text("""
        SELECT a.attname AS name, format_type(a.atttypid,a.atttypmod) AS type,
               format_type(a.atttypid,-1) AS comparison_type, a.atttypid AS type_id,
               a.attcollation AS collation,
               CASE WHEN k.position <= i.indnkeyatts THEN k.position END AS pk
        FROM pg_attribute a LEFT JOIN pg_index i ON i.indrelid=a.attrelid AND i.indisprimary AND i.indisvalid
        LEFT JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,position) ON k.attnum=a.attnum
        WHERE a.attrelid=CAST(:oid AS oid) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum
    """), {"oid": info["oid"]})).mappings().all()
    primary = [c["name"] for c in sorted((c for c in columns if c["pk"]), key=lambda c: c["pk"])]
    only_heap = info["relkind"] == "r" and not info["has_children"]
    return {**info, "from": ("ONLY " if only_heap else "") + qualified,
            "small": only_heap and info["small_heap"], "primary": primary, "columns": {c["name"]: c for c in columns}}


def _safe_plan(plan):
    """Large tables require index conditions, streaming order and bounded partition fanout."""
    if isinstance(plan, str):
        plan = json.loads(plan)
    pending = [plan[0]["Plan"]]
    seen = scans = 0
    while pending:
        node = pending.pop()
        seen += 1
        if seen > 128:
            return False
        kind = node["Node Type"]
        if kind in ("Index Scan", "Index Only Scan"):
            if not node.get("Index Cond") or "Filter" in node:
                return False
            scans += 1
        elif kind == "Custom Scan":
            if node.get("Custom Plan Provider") != "ChunkAppend" or not node.get("Plans"):
                return False
        elif kind not in ("Limit", "Result", "Append", "Merge Append", "LockRows"):
            return False
        pending.extend(node.get("Plans", ()))
    return bool(scans)


async def _lookup(session, asset, fields, values, projection, *, types=None, order=False, limit=1, offset=0, lock=False):
    # Every type expression comes from pg_catalog.format_type. Cast bound text
    # to the native type without typmod so a longer key cannot silently truncate.
    types = types or [asset["columns"][k]["comparison_type"] for k in fields]
    names = [f"t.{quote_identifier(k)}" for k in fields]
    args = [f"CAST(CAST(:v{i} AS text) AS {typ})" for i, typ in enumerate(types)]
    predicate = " AND ".join(f"{name} = {arg}" for name, arg in zip(names, args, strict=True))
    if asset["hash_partitioned"] and fields == asset["primary"] and len(fields) > 1:
        # A point range on the compound key seeks its ordered index. Separate
        # equalities can instead choose a low-cardinality secondary index and
        # filter every matching source to locate a single record.
        cols, bound = "ROW(" + ", ".join(names) + ")", "ROW(" + ", ".join(args) + ")"
        predicate = f"{names[0]} = {args[0]} AND {cols} >= {bound} AND {cols} <= {bound}"
    ordering = " ORDER BY " + ", ".join(f"t.{quote_identifier(k)}" for k in asset["primary"]) if order else ""
    sql = f"SELECT {projection} FROM {asset['from']} t WHERE {predicate}{ordering} LIMIT :limit OFFSET :offset"
    if lock:
        sql += " FOR UPDATE OF t"
    params = {"limit": limit, "offset": offset, **{f"v{i}": str(values[k]) for i, k in enumerate(fields)}}
    if not asset["small"]:
        await session.execute(text("SET LOCAL enable_seqscan=off"))
        plan = (await session.execute(text("EXPLAIN (FORMAT JSON) " + sql), params)).scalar_one()
        if not _safe_plan(plan):
            return None
    return (await session.execute(text(sql), params)).all()


def _lossless_number(token):
    if len(token.lstrip("-")) > 16:
        return token
    value = int(token)
    return value if abs(value) <= 2**53 - 1 else token


def _json_float(token):
    value = float(token)
    return value if isfinite(value) else token


async def _source(session, asset, key, projection, *, lock=False):
    if not asset["primary"]:
        raise HTTPException(409, "This table has no primary key for a stable record address")
    if set(asset["primary"]) != set(key):
        raise HTTPException(422, "Provide the complete primary key")
    records = await _lookup(session, asset, asset["primary"], key, projection, lock=lock)
    if records is None:
        raise HTTPException(409, "The record requires a bounded indexed lookup")
    if not records:
        raise HTTPException(404, "Record not found")
    return records[0]


async def _limits(session):
    await session.execute(text("SET LOCAL statement_timeout='1500ms'"))
    await session.execute(text("SET LOCAL lock_timeout='250ms'"))


async def row_by_key(layer, table, key, *, editable):
    schema = _object_schema(layer)
    async with get_etl_layers().session(repeatable_read=True) as session:
        await _limits(session)
        asset = await _asset(session, schema, table)
        projection = "to_jsonb(t)::text, " + row_identity_sql(asset["primary"])
        if editable:
            projection += ", " + ROW_VERSION_SQL
        record = await _source(session, asset, key, projection)
        # Preserve SQL decimal scale and bigint precision at the JavaScript boundary.
        row = json.loads(record[0], parse_float=str, parse_int=_lossless_number)
        json_fields = [
            name for name, column in asset["columns"].items()
            if column["type"] in ("json", "jsonb", "json[]", "jsonb[]") and row[name] is not None
        ]
        if json_fields:
            # JSON documents retain finite JSON numbers, including geometry coordinates.
            document = json.loads(record[0], parse_float=_json_float, parse_int=_lossless_number)
            row.update((name, document[name]) for name in json_fields)
        for name, column in asset["columns"].items():
            if column["type"] in ("real", "double precision") and row[name] is not None:
                native = Decimal(str(row[name]))
                if native.is_finite():
                    row[name] = float(native)
        result = {"row": row, "identity": record[1], "primaryKey": asset["primary"],
                  "columnTypes": {k: v["type"] for k, v in asset["columns"].items()}}
        if editable:
            result.update(rowVersion=record[2], rowActions={"edit": True, "delete": allows_physical_delete(schema)})
        return result


async def row_links(layer, table, query, *, can_manage=False):
    from app.web.services.ontology_object_links import read_row_links

    return await read_row_links(layer, table, query, can_manage=can_manage)
