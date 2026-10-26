"""Persisted workspace writes for the PostgreSQL ETL browser."""

from __future__ import annotations

import json
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from anyio import fail_after
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.storage.postgres_client import get_pg_client
from app.web.core.config import settings as web_settings
from app.web.services.etl_layers import (
    LAYERS,
    get_etl_layers,
)
from app.web.services.etl_layers import (
    quote_identifier as _quote_identifier,
)
from app.web.services.etl_layers import (
    resolve_schema as _resolve_schema,
)


def quote_identifier(name: Any) -> str:
    if not isinstance(name, str):
        _bad("PostgreSQL identifiers must be strings")
    return _quote_identifier(name)


def resolve_schema(layer: Any) -> str:
    if not isinstance(layer, str):
        _bad("Schema must be a string")
    return _resolve_schema(layer)


def _bad(message: str) -> None:
    raise HTTPException(422, message)


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError):
        _bad("Values must be valid JSON")


def _literal(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _names(value: Any, known: set[str] | None = None) -> list[str]:
    names = [item.strip() for item in value.split(",")] if isinstance(value, str) else value
    if not isinstance(names, list) or not names or any(not isinstance(item, str) for item in names):
        _bad("Select one or more distinct columns")
    if len(set(names)) != len(names):
        _bad("Select one or more distinct columns")
    for name in names:
        quote_identifier(name)
        if known is not None and name not in known:
            _bad(f"Unknown column: {name}")
    return names


_TYPES = {
    "TEXT", "BIGINT", "INTEGER", "SMALLINT", "BOOLEAN", "TIMESTAMPTZ", "TIMESTAMP",
    "DATE", "TIME", "JSONB", "JSON", "UUID", "NUMERIC", "DECIMAL", "REAL",
    "DOUBLE PRECISION", "BYTEA", "VARCHAR", "CHAR", "SERIAL", "BIGSERIAL",
}


def _column_type(field: dict[str, Any]) -> str:
    value = " ".join(str(field.get("type", "TEXT")).upper().split())
    if value not in _TYPES:
        _bad(f"Unsupported PostgreSQL type: {value}")
    length = field.get("length")
    if length not in (None, "", 0, "0"):
        if value not in {"VARCHAR", "CHAR", "NUMERIC", "DECIMAL"}:
            _bad(f"{value} does not accept a length")
        if not re.fullmatch(r"[1-9][0-9]{0,5}", str(length)):
            _bad("Invalid type length")
        value += f"({int(length)})"
    return value


_CHECK_TOKEN = re.compile(r"\s+|'(?:[^']|'')*'|\d+(?:\.\d+)?|[a-zA-Z_][a-zA-Z_0-9]*|<>|!=|<=|>=|[(),+*/%<>=-]")
_CHECK_WORDS = {"AND", "OR", "NOT", "NULL", "TRUE", "FALSE", "IS", "IN", "BETWEEN", "LIKE", "ILIKE"}
_CHECK_FUNCTIONS = {"LENGTH", "CHAR_LENGTH", "LOWER", "UPPER", "COALESCE", "ABS"}


def _check_expression(expression: Any, fields: set[str]) -> str:
    """Allow scalar checks only; never accept statements, subqueries or arbitrary calls."""
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 4000:
        _bad("Check expression is required (maximum 4000 characters)")
    tokens: list[str] = []
    position = 0
    depth = 0
    while position < len(expression):
        match = _CHECK_TOKEN.match(expression, position)
        if not match:
            _bad("Unsupported token in check expression")
        token = match.group()
        position = match.end()
        if token.isspace():
            continue
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
            if depth < 0:
                _bad("Unbalanced check expression")
        elif re.fullmatch(r"[a-zA-Z_][a-zA-Z_0-9]*", token):
            upper = token.upper()
            if upper in _CHECK_WORDS:
                token = upper
            elif upper in _CHECK_FUNCTIONS and expression[position:].lstrip().startswith("("):
                token = f"pg_catalog.{upper.lower()}" if upper != "COALESCE" else upper
            elif token in fields:
                if expression[position:].lstrip().startswith("("):
                    _bad(f"Unsupported function in check: {token}")
                token = quote_identifier(token)
            else:
                _bad(f"Unknown column or unsupported function in check: {token}")
        tokens.append(token)
    if depth:
        _bad("Unbalanced check expression")
    return " ".join(tokens)


def build_table_statements(schema: str, payload: dict[str, Any]) -> list[str]:
    """Build DDL from structured controls, with no user-supplied SQL fragments."""
    table = payload.get("name", "")
    qualified = f"{quote_identifier(schema)}.{quote_identifier(table)}"
    options = payload.get("options") or {}
    if not isinstance(options, dict) or any(value for key, value in options.items() if key != "comment"):
        _bad("MySQL table options are not supported by PostgreSQL")
    fields = payload.get("fields")
    if not isinstance(fields, list) or not fields or len(fields) > 200:
        _bad("A table requires 1 to 200 fields")
    if any(not isinstance(field, dict) for field in fields):
        _bad("Fields must be objects")
    fields = [field for field in fields if field.get("name")]
    if not fields:
        _bad("A table requires at least one named field")
    for field in fields:
        quote_identifier(field["name"])
    known = {field["name"] for field in fields}
    if len(known) != len(fields):
        _bad("Field names must be distinct")
    definitions: list[str] = []
    primary: list[str] = []
    comments: list[str] = []
    for field in fields:
        name = quote_identifier(field["name"])
        definition = f"{name} {_column_type(field)}"
        if not field.get("nullable", True) or field.get("primary", False):
            definition += " NOT NULL"
        definitions.append(definition)
        if field.get("primary"):
            primary.append(name)
        if field.get("comment"):
            comments.append(f"COMMENT ON COLUMN {qualified}.{name} IS {_literal(field['comment'])}")
    if primary:
        definitions.append(f"PRIMARY KEY ({', '.join(primary)})")
    constraint_names: set[str] = set()
    for collection in ("checks", "foreignKeys", "indexes"):
        entries = payload.get(collection, [])
        if not isinstance(entries, list) or len(entries) > 200 or any(not isinstance(entry, dict) for entry in entries):
            _bad(f"{collection} must be an array of objects (maximum 200)")
    for check in payload.get("checks", []):
        if not check.get("name") and not check.get("expression"):
            continue
        name = quote_identifier(check.get("name", ""))
        if name in constraint_names:
            _bad("Constraint names must be distinct")
        constraint_names.add(name)
        definitions.append(f"CONSTRAINT {name} CHECK ({_check_expression(check.get('expression'), known)})")
        if check.get("comment"):
            comments.append(f"COMMENT ON CONSTRAINT {name} ON {qualified} IS {_literal(check['comment'])}")
    actions = {"RESTRICT", "CASCADE", "SET NULL", "NO ACTION", "SET DEFAULT"}
    for foreign in payload.get("foreignKeys", []):
        if not foreign.get("name") and not foreign.get("fields"):
            continue
        name = quote_identifier(foreign.get("name", ""))
        if name in constraint_names:
            _bad("Constraint names must be distinct")
        constraint_names.add(name)
        local = _names(foreign.get("fields"), known)
        remote = _names(foreign.get("refFields"))
        if len(local) != len(remote):
            _bad("Foreign key and referenced column counts must match")
        delete = " ".join(str(foreign.get("onDelete", "RESTRICT")).upper().split())
        update = " ".join(str(foreign.get("onUpdate", "RESTRICT")).upper().split())
        if delete not in actions or update not in actions:
            _bad("Invalid foreign key action")
        ref = quote_identifier(foreign.get("refTable", ""))
        definitions.append(
            f"CONSTRAINT {name} FOREIGN KEY ({', '.join(map(quote_identifier, local))}) "
            f"REFERENCES {quote_identifier(schema)}.{ref} ({', '.join(map(quote_identifier, remote))}) "
            f"ON DELETE {delete} ON UPDATE {update}"
        )
    statements = [f"CREATE TABLE {qualified} ({', '.join(definitions)})"]
    index_names: set[str] = set()
    for index in payload.get("indexes", []):
        if not index.get("name") and not index.get("fields"):
            continue
        name = quote_identifier(index.get("name", ""))
        if name in index_names:
            _bad("Index names must be distinct")
        index_names.add(name)
        method = str(index.get("type", "BTREE")).upper()
        if method not in {"BTREE", "HASH", "GIN", "GIST", "BRIN"}:
            _bad("Unsupported index method")
        if index.get("unique") and method != "BTREE":
            _bad("Unique indexes require BTREE")
        columns = ", ".join(map(quote_identifier, _names(index.get("fields"), known)))
        statements.append(f"CREATE {'UNIQUE ' if index.get('unique') else ''}INDEX {name} ON {qualified} USING {method} ({columns})")
        if index.get("comment"):
            comments.append(f"COMMENT ON INDEX {quote_identifier(schema)}.{name} IS {_literal(index['comment'])}")
    description = payload.get("description") or options.get("comment")
    if description:
        comments.append(f"COMMENT ON TABLE {qualified} IS {_literal(description)}")
    return statements + comments


_CONNECTION_DDL = """
CREATE TABLE IF NOT EXISTS public.flow_connections (
    id text PRIMARY KEY, name text NOT NULL, database_name text NOT NULL,
    schemas jsonb NOT NULL DEFAULT '[]'::jsonb
)
"""
_PUBLICATION_DDL = """
CREATE TABLE IF NOT EXISTS public.flow_publications (
    id text PRIMARY KEY, token text NOT NULL UNIQUE, schema_name text NOT NULL,
    table_name text NOT NULL, valid_from timestamptz NOT NULL,
    valid_until timestamptz NOT NULL, scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
)
"""
_PIPELINE_SCHEMAS = {"ts_meta", *(schema for layer in LAYERS for schema in (f"ts_{layer}", f"ts_{layer}_hist"))}


class EtlWorkspace:
    def __init__(self) -> None:
        self._pg = get_pg_client()
        self._layers = get_etl_layers()

    @asynccontextmanager
    async def _session(self):
        try:
            with fail_after(12):
                await self._pg.connect(max_retries=1)
                async with self._pg.session() as session:
                    await session.execute(text("SET LOCAL statement_timeout = '10s'"))
                    yield session
        except HTTPException:
            raise
        except DBAPIError as error:
            code = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
            if code in {"23505", "42P07", "42P06", "42710", "2BP01", "23503", "3F000"}:
                raise HTTPException(409, "Database object conflicts with existing data or dependencies") from error
            if code and (code.startswith("22") or code.startswith("23") or code.startswith("42")):
                raise HTTPException(422, "PostgreSQL rejected the definition or value") from error
            raise HTTPException(503, "PostgreSQL operation failed or timed out") from error
        except (ConnectionError, OSError, TimeoutError) as error:
            raise HTTPException(503, "PostgreSQL is unavailable") from error

    async def _exists(self, session, table: str) -> bool:
        result = await session.execute(text("SELECT to_regclass(:name) IS NOT NULL AS present"), {"name": f"public.{table}"})
        return bool(result.scalar_one())

    async def _schema_exists(self, session, schema: str) -> None:
        result = await session.execute(text("SELECT 1 FROM pg_namespace WHERE nspname = :schema"), {"schema": schema})
        if result.scalar_one_or_none() is None:
            raise HTTPException(404, "Schema not found")

    async def connections(self) -> list[dict[str, Any]]:
        async with self._session() as session:
            if await self._exists(session, "flow_connections"):
                result = await session.execute(text('SELECT id, name, database_name AS database, schemas FROM public.flow_connections ORDER BY name'))
                rows = [dict(row) for row in result.mappings()]
                return rows
            result = await session.execute(text("SELECT current_database()"))
            database = result.scalar_one()
            result = await session.execute(text("SELECT nspname FROM pg_namespace WHERE left(nspname, 3) = 'ts_' ORDER BY nspname"))
            return [{"id": "configured", "name": "PostgreSQL", "database": database, "schemas": list(result.scalars())}]

    async def save_connection(self, payload: dict[str, Any], connection_id: str | None = None) -> dict[str, Any]:
        name = str(payload.get("name", "")).strip()
        if not name or len(name) > 200:
            _bad("Connection name is required (maximum 200 characters)")
        schemas = payload.get("schemas", [])
        if not isinstance(schemas, list) or any(not isinstance(schema, str) for schema in schemas):
            _bad("Schemas must be a list of distinct names")
        if len(schemas) != len(set(schemas)):
            _bad("Schemas must be a list of distinct names")
        schemas = [resolve_schema(schema) for schema in schemas]
        async with self._session() as session:
            result = await session.execute(text("SELECT current_database()"))
            database = result.scalar_one()
            if payload.get("database") != database:
                _bad("Only the configured PostgreSQL database can be connected")
            for schema in schemas:
                await session.execute(text(f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(schema)}"))
            await session.execute(text(_CONNECTION_DDL))
            identifier = connection_id or str(uuid.uuid4())
            if connection_id and connection_id != "configured":
                result = await session.execute(text("SELECT id FROM public.flow_connections WHERE id = :id"), {"id": connection_id})
                if result.scalar_one_or_none() is None:
                    raise HTTPException(404, "Saved connection not found")
            result = await session.execute(text("""
                INSERT INTO public.flow_connections (id, name, database_name, schemas)
                VALUES (:id, :name, :database, CAST(:schemas AS jsonb))
                ON CONFLICT (id) DO UPDATE SET name = excluded.name,
                    database_name = excluded.database_name, schemas = excluded.schemas
                RETURNING id, name, database_name AS database, schemas
            """), {"id": identifier, "name": name, "database": database, "schemas": _json(schemas)})
            return dict(result.mappings().one())

    async def delete_connection(self, connection_id: str) -> dict[str, Any]:
        async with self._session() as session:
            if not await self._exists(session, "flow_connections"):
                if connection_id != "configured":
                    raise HTTPException(404, "Saved connection not found")
                await session.execute(text(_CONNECTION_DDL))
                return {"deleted": True, "id": connection_id}
            result = await session.execute(text("DELETE FROM public.flow_connections WHERE id = :id RETURNING id"), {"id": connection_id})
            if result.scalar_one_or_none() is None:
                raise HTTPException(404, "Saved connection not found")
        return {"deleted": True, "id": connection_id}

    async def rename_schema(self, layer: str, payload: dict[str, Any]) -> dict[str, Any]:
        schema = resolve_schema(layer)
        target = resolve_schema(payload.get("name") or schema)
        if target != schema and schema in _PIPELINE_SCHEMAS:
            raise HTTPException(409, "Pipeline schemas cannot be renamed; set an alias instead")
        if not any(key in payload for key in ("name", "alias")):
            _bad("Provide a schema name or alias")
        alias = payload.get("alias")
        if alias is not None and (not isinstance(alias, str) or len(alias) > 200):
            _bad("Schema alias must be a string of at most 200 characters")
        async with self._session() as session:
            await self._schema_exists(session, schema)
            if target != schema:
                await session.execute(text(f"ALTER SCHEMA {quote_identifier(schema)} RENAME TO {quote_identifier(target)}"))
                if await self._exists(session, "flow_publications"):
                    await session.execute(text("UPDATE public.flow_publications SET schema_name = :target WHERE schema_name = :schema"), {"target": target, "schema": schema})
                if await self._exists(session, "flow_handler_drafts"):
                    await session.execute(text("UPDATE public.flow_handler_drafts SET schema_name = :target WHERE schema_name = :schema"), {"target": target, "schema": schema})
                if await self._exists(session, "flow_connections"):
                    await session.execute(text("""
                        UPDATE public.flow_connections SET schemas = (
                            SELECT jsonb_agg(CASE WHEN value = :schema THEN :target ELSE value END)
                            FROM jsonb_array_elements_text(schemas)
                        ) WHERE schemas @> CAST(:old AS jsonb)
                    """), {"schema": schema, "target": target, "old": _json([schema])})
            if alias is not None:
                await session.execute(text(f"COMMENT ON SCHEMA {quote_identifier(target)} IS {_literal(alias)}".replace(":", r"\:")))
        return {"key": target, "name": target, "alias": alias}

    async def delete_schema(self, layer: str) -> dict[str, Any]:
        schema = resolve_schema(layer)
        if schema in _PIPELINE_SCHEMAS:
            raise HTTPException(409, "Pipeline schemas cannot be deleted")
        async with self._session() as session:
            await self._schema_exists(session, schema)
            await session.execute(text(f"DROP SCHEMA {quote_identifier(schema)} RESTRICT"))
            if await self._exists(session, "flow_publications"):
                await session.execute(text("DELETE FROM public.flow_publications WHERE schema_name = :schema"), {"schema": schema})
            if await self._exists(session, "flow_handler_drafts"):
                await session.execute(text("DELETE FROM public.flow_handler_drafts WHERE schema_name = :schema"), {"schema": schema})
            if await self._exists(session, "flow_connections"):
                await session.execute(text("UPDATE public.flow_connections SET schemas = schemas - :schema"), {"schema": schema})
        return {"deleted": True, "name": schema}

    async def create_table(self, layer: str, payload: dict[str, Any]) -> dict[str, Any]:
        schema = resolve_schema(layer)
        statements = build_table_statements(schema, payload)
        async with self._session() as session:
            await self._schema_exists(session, schema)
            for statement in statements:
                await session.execute(text(statement.replace(":", r"\:")))
        return await self._layers.get_table(schema, payload["name"])

    async def _write_row(self, layer: str, table: str, payload: dict[str, Any], delete: bool) -> dict[str, Any]:
        schema = resolve_schema(layer)
        qualified = f"{quote_identifier(schema)}.{quote_identifier(table)}"
        columns = await self._layers.columns(schema, table)
        primary = [column["name"] for column in columns if column.get("primary")]
        key = payload.get("key")
        if not primary:
            raise HTTPException(409, "Rows without a primary key cannot be edited or deleted")
        if not isinstance(key, dict) or set(key) != set(primary) or any(value is None for value in key.values()):
            _bad("The complete primary key is required")
        changes = payload.get("changes", payload.get("values"))
        known = {column["name"] for column in columns}
        if not delete and (not isinstance(changes, dict) or not changes or set(changes) - known):
            _bad("Provide changes for known columns")
        # json_populate_record converts JSON strings to native dates, UUIDs, arrays and JSON values.
        predicate = " AND ".join(f"t.{quote_identifier(name)} = k.{quote_identifier(name)}" for name in primary)
        source = f"jsonb_populate_record(NULL::{qualified}, CAST(:key AS jsonb)) AS k"
        if delete:
            sql = f"DELETE FROM {qualified} AS t USING {source} WHERE {predicate} RETURNING to_jsonb(t) AS row"
        else:
            assignments = ", ".join(f"{quote_identifier(name)} = v.{quote_identifier(name)}" for name in changes)
            sql = f"UPDATE {qualified} AS t SET {assignments} FROM {source}, jsonb_populate_record(NULL::{qualified}, CAST(:changes AS jsonb)) AS v WHERE {predicate} RETURNING to_jsonb(t) AS row"
        async with self._session() as session:
            result = await session.execute(text(sql), {"key": _json(key), "changes": _json(changes)})
            rows = list(result.mappings())
            if not rows:
                raise HTTPException(404, "Row not found")
            if len(rows) != 1:
                raise HTTPException(409, "Primary key matched multiple rows; write rolled back")
            return {"row": rows[0]["row"], "rowCount": 1, "deleted": delete}

    async def patch_row(self, layer: str, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._write_row(layer, table, payload, False)

    async def delete_row(self, layer: str, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._write_row(layer, table, payload, True)

    @staticmethod
    def _publication(row: Any) -> dict[str, Any]:
        record = dict(row)
        scope = record.pop("scope")
        return {**record, **scope, "url": f"{web_settings.API_V1_STR}/etl/published/{record['token']}"}

    async def publications(self, layer: str, table: str) -> list[dict[str, Any]]:
        schema = resolve_schema(layer)
        await self._layers.get_table(schema, table)
        async with self._session() as session:
            if not await self._exists(session, "flow_publications"):
                return []
            result = await session.execute(text('SELECT id, token, valid_from AS "validFrom", valid_until AS "validUntil", scope FROM public.flow_publications WHERE schema_name = :schema AND table_name = :table ORDER BY created_at'), {"schema": schema, "table": table})
            return [self._publication(row) for row in result.mappings()]

    @staticmethod
    def _validity(value: Any) -> datetime:
        if not isinstance(value, str):
            _bad("Publication validity dates are required")
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            _bad("Invalid publication date")
        # The datetime-local control uses the application's Asia/Shanghai timezone.
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return stamp.astimezone(timezone.utc)

    async def save_publication(self, layer: str, table: str, payload: dict[str, Any], publication_id: str | None = None) -> dict[str, Any]:
        schema = resolve_schema(layer)
        columns = await self._layers.columns(schema, table)
        start = self._validity(payload.get("validFrom"))
        end = self._validity(payload.get("validUntil"))
        if end <= start:
            _bad("Publication validUntil must be later than validFrom")
        scope = {name: payload.get(name, []) for name in ("filterType", "filterSource", "filters", "sorts")}
        scope["search"] = payload.get("search", "")
        scope["partition"] = payload.get("partition")
        if not isinstance(scope["search"], str) or len(scope["search"]) > 1000:
            _bad("Publication search must be a string of at most 1000 characters")
        if scope["partition"] is not None:
            quote_identifier(scope["partition"])
        for name in ("filterType", "filterSource"):
            if not isinstance(scope[name], list) or len(scope[name]) > 100 or any(not isinstance(value, str) or len(value) > 500 for value in scope[name]):
                _bad(f"Invalid {name}")
        if not isinstance(scope["filters"], list) or not isinstance(scope["sorts"], list):
            _bad("Filters and sorts must be arrays")
        known = {column["name"] for column in columns}
        for name, column in (("filterType", "data_type"), ("filterSource", "data_source")):
            if scope[name] and column not in known:
                _bad(f"Publication range requires column {column}")
        # Validate the stored query through the same path that will serve the token.
        await self._layers.data(schema, table, {"limit": 1}, scope=scope)
        async with self._session() as session:
            await session.execute(text(_PUBLICATION_DDL))
            params = {"id": publication_id or str(uuid.uuid4()), "schema": schema, "table": table, "start": start, "end": end, "scope": _json(scope)}
            if publication_id:
                sql = 'UPDATE public.flow_publications SET valid_from = :start, valid_until = :end, scope = CAST(:scope AS jsonb) WHERE id = :id AND schema_name = :schema AND table_name = :table RETURNING id, token, valid_from AS "validFrom", valid_until AS "validUntil", scope'
            else:
                params["token"] = "pk_live_" + secrets.token_urlsafe(32)
                sql = 'INSERT INTO public.flow_publications (id, token, schema_name, table_name, valid_from, valid_until, scope) VALUES (:id, :token, :schema, :table, :start, :end, CAST(:scope AS jsonb)) RETURNING id, token, valid_from AS "validFrom", valid_until AS "validUntil", scope'
            result = await session.execute(text(sql), params)
            row = result.mappings().one_or_none()
            if row is None:
                raise HTTPException(404, "Publication not found")
            return self._publication(row)

    async def delete_publication(self, layer: str, table: str, publication_id: str) -> dict[str, Any]:
        schema = resolve_schema(layer)
        async with self._session() as session:
            if not await self._exists(session, "flow_publications"):
                raise HTTPException(404, "Publication not found")
            result = await session.execute(text("DELETE FROM public.flow_publications WHERE id = :id AND schema_name = :schema AND table_name = :table RETURNING id"), {"id": publication_id, "schema": schema, "table": table})
            if result.scalar_one_or_none() is None:
                raise HTTPException(404, "Publication not found")
        return {"deleted": True, "id": publication_id}

    async def published_data(self, token: str, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._session() as session:
            if not await self._exists(session, "flow_publications"):
                raise HTTPException(404, "Publication not found")
            result = await session.execute(text("SELECT schema_name, table_name, scope FROM public.flow_publications WHERE token = :token AND valid_from <= now() AND valid_until > now()"), {"token": token})
            row = result.mappings().one_or_none()
            if row is None:
                raise HTTPException(404, "Publication not found or outside its validity window")
            record = dict(row)
        # Only pagination is public input. The stored filters and sort order are authoritative.
        query = {name: payload[name] for name in ("limit", "offset") if name in payload}
        return await self._layers.data(record["schema_name"], record["table_name"], query, scope=record["scope"])


@lru_cache
def get_etl_workspace() -> EtlWorkspace:
    return EtlWorkspace()
