"""Persisted workspace writes for the PostgreSQL ETL browser."""

from __future__ import annotations

import hashlib
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

from app.etl.schema.catalog_metadata import catalog_logical_layers
from app.etl.schema.object_relations import relation_mapping, validate_definition
from app.etl.schema.ontology import sync_object_metadata
from app.storage.postgres_client import get_pg_client
from app.storage.business_database import BusinessDatabaseNotConfigured, get_business_pg_client
from app.web.api.models.push_inbox_apply import PUSH_APPLY_SCHEMA
from app.web.services.batch_snapshot_range_scope import BATCH_RANGE_SOURCE_SCHEMA, BATCH_RANGE_TARGET
from app.web.services.pipeline_handoff_scope import HANDOFF_PRIVATE_SCHEMA
from app.web.services.snapshot_intent_scope import SNAPSHOT_INTENT_PRIVATE_SCHEMA
from app.web.core.config import settings as web_settings
from app.web.services.etl_layers import (
    EtlLayers,
    INDEX_SANDBOX_SCHEMA,
    INDEX_SANDBOX_TABLE,
    RESERVED_BATCH_TARGET,
    row_version_sql,
    allows_physical_delete,
    get_etl_layers,
    is_ordinary_schema,
    ordinary_schema_sql,
    quote_schema_identifier,
    ordinary_table_sql,
    require_ordinary_table,
    reserved_batch_target_exists,
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


def _publication_name(value: Any) -> str:
    name = value.strip() if isinstance(value, str) else ""
    if not 1 <= len(name) <= 120 or re.search(r"[\x00\ud800-\udfff]", name):
        _bad("Publication name must be a string of 1 to 120 characters")
    return name


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
    if "foreignKeys" in payload:
        _bad("Physical foreign keys are not supported; define ontology relations instead")
    table = payload.get("name", "")
    qualified = f"{quote_schema_identifier(schema)}.{quote_identifier(table)}"
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
    for collection in ("checks", "indexes"):
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
    statements = [f"CREATE TABLE {qualified} ({', '.join(definitions)})"]
    index_names: set[str] = set()
    index_definitions: set[tuple] = set()
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
        fields_in_index = _names(index.get("fields"), known)
        if method == "HASH" and len(fields_in_index) != 1:
            _bad("HASH indexes require exactly one property")
        definition = (method, tuple(fields_in_index), bool(index.get("unique")))
        if definition in index_definitions:
            _bad("Duplicate index definition: method, ordered properties and uniqueness must differ")
        index_definitions.add(definition)
        columns = ", ".join(map(quote_identifier, fields_in_index))
        statements.append(f"CREATE {'UNIQUE ' if index.get('unique') else ''}INDEX {name} ON {qualified} USING {method} ({columns})")
        if index.get("comment"):
            comments.append(f"COMMENT ON INDEX {quote_schema_identifier(schema)}.{name} IS {_literal(index['comment'])}")
    description = payload.get("description") or options.get("comment")
    if description:
        comments.append(f"COMMENT ON TABLE {qualified} IS {_literal(description)}")
    return statements + comments


def table_relations(schema: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    definitions = payload.get("relations", [])
    if not isinstance(definitions, list) or len(definitions) > 200:
        _bad("Relations must be an array (maximum 200)")
    allowed = {"name", "targetSchema", "targetTable", "sourceFields", "targetFields", "cardinality", "description"}
    known = {field["name"] for field in payload["fields"] if field.get("name")}
    rows = []
    for definition in definitions:
        if not isinstance(definition, dict) or set(definition) - allowed:
            _bad("A relation must contain only the documented metadata fields")
        try:
            row = validate_definition({"name": definition.get("name"), "source_schema": schema,
                "source_table": payload["name"], "source_fields": definition.get("sourceFields"),
                "target_schema": definition.get("targetSchema"), "target_table": definition.get("targetTable"),
                "target_fields": definition.get("targetFields"), "cardinality": definition.get("cardinality"),
                "description": definition.get("description", ""), "relationship_kind": "association",
                "provenance": "ontology:create_table"})
        except ValueError as error:
            _bad(str(error))
        if not set(row["source_fields"]).issubset(known):
            _bad("A relation references an unknown source property")
        rows.append(row)
    if len({row["name"] for row in rows}) != len(rows):
        _bad("Relation names must be distinct")
    mappings = [relation_mapping({**row, "columns": row["source_fields"],
                                 "referenced_columns": row["target_fields"]}) for row in rows]
    if len(set(mappings)) != len(mappings):
        _bad("Duplicate relation property mapping, including a reverse definition")
    return rows


_CONNECTION_DDL = """
CREATE TABLE IF NOT EXISTS app.flow_connections (
    id text PRIMARY KEY, name text NOT NULL, database_name text NOT NULL,
    schemas jsonb NOT NULL DEFAULT '[]'::jsonb
)
"""
_PUBLICATION_DDL = """
CREATE TABLE IF NOT EXISTS app.flow_publications (
    id text PRIMARY KEY, token text NOT NULL UNIQUE, schema_name text NOT NULL,
    table_name text NOT NULL, valid_from timestamptz NOT NULL,
    valid_until timestamptz NOT NULL, scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
)
"""
_PIPELINE_LAYERS = ("rds", "ods", "task", "dwd", "dws", "dim", "ads")


class EtlWorkspace:
    def __init__(self) -> None:
        self._pg = get_pg_client()
        self._business_pg = get_business_pg_client()
        self._layers = get_etl_layers()

    @asynccontextmanager
    async def _session(self, *, business=False, schema=None, table=None, connection_id=None, logical_layer=None):
        try:
            with fail_after(12):
                if business and (schema is not None or logical_layer is not None or connection_id is not None):
                    pg, layers = await self._layers.scoped_postgres(
                        schema, table, connection_id=connection_id, logical_layer=logical_layer,
                    )
                else:
                    pg, layers = (self._business_pg if business else self._pg), None
                token = catalog_logical_layers.set(layers) if business else None
                try:
                    await pg.connect(max_retries=1)
                    async with pg.session() as session:
                        await session.execute(text("SET LOCAL statement_timeout = '10s'"))
                        yield session
                finally:
                    if token is not None:
                        catalog_logical_layers.reset(token)
        except HTTPException:
            raise
        except BusinessDatabaseNotConfigured as error:
            raise HTTPException(409, str(error)) from None
        except DBAPIError as error:
            code = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
            metadata_statement = re.search(
                r"\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+(?:ONLY\s+)?(?:app\.)?(?:helio_|flow_)",
                str(error.statement), re.IGNORECASE,
            )
            if code == "42P01" and (not business or metadata_statement):
                raise HTTPException(503, "Ontology metadata is not initialized") from error
            if code in {"23505", "42P07", "42P06", "42710", "2BP01", "23503", "3F000"}:
                raise HTTPException(409, "Database object conflicts with existing data or dependencies") from error
            if code and (code.startswith("22") or code.startswith("23") or code.startswith("42")):
                raise HTTPException(422, "PostgreSQL rejected the definition or value") from error
            raise HTTPException(503, "PostgreSQL operation failed or timed out") from error
        except (ConnectionError, OSError, TimeoutError) as error:
            raise HTTPException(503, "PostgreSQL is unavailable") from error

    async def _exists(self, session, table: str) -> bool:
        result = await session.execute(text("SELECT to_regclass(:name) IS NOT NULL AS present"), {"name": f"app.{table}"})
        return bool(result.scalar_one())

    async def _schema_exists(self, session, schema: str) -> None:
        result = await session.execute(text("SELECT 1 FROM pg_namespace WHERE nspname = :schema"), {"schema": schema})
        if result.scalar_one_or_none() is None:
            raise HTTPException(404, "Schema not found")

    @staticmethod
    async def _require_schema_write_scope(session, schema: str) -> None:
        layers = catalog_logical_layers.get()
        if layers is None:
            return
        conflict = await session.scalar(text("""SELECT EXISTS (
            SELECT 1 FROM app.helio_objects WHERE schema_name = :schema
              AND NOT (layer = ANY(CAST(:layers AS text[])))
        )"""), {"schema": schema, "layers": list(layers)})
        if conflict:
            raise HTTPException(409, "The schema name is recorded on another connection; its references are ambiguous")

    async def connections(self) -> list[dict[str, Any]]:
        database = await self._layers.database_name()
        async with self._session() as session:
            if await self._exists(session, "flow_connections"):
                result = await session.execute(text('SELECT id, name, database_name AS database, schemas FROM app.flow_connections WHERE database_name=:database ORDER BY name'), {"database": database})
                rows = [dict(row) for row in result.mappings()]
                requested = sorted({schema for row in rows for schema in row["schemas"] if is_ordinary_schema(schema)})
                async with self._session(business=True) as catalog_session:
                    result = await catalog_session.execute(text(f"""SELECT nspname FROM pg_namespace n
                        WHERE nspname = ANY(CAST(:schemas AS text[])) AND {ordinary_schema_sql()}
                    """), {"schemas": requested})
                    accessible = set(result.scalars())
                for row in rows:
                    row["schemas"] = [schema for schema in row["schemas"] if schema in accessible]
                return rows
        schemas = [row["schemaName"] for row in await self._layers.layers()]
        return [{"id": "warehouse", "name": "Business database", "database": database, "schemas": schemas}]

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
        connection, _ = await self._layers.connection_scope()
        async with self._session() as session, self._session(
            business=True, connection_id=str(connection["id"]),
        ) as catalog_session:
            result = await catalog_session.execute(text("SELECT current_database()"))
            database = result.scalar_one()
            if payload.get("database") != database:
                _bad("Select the business database through Data Connections")
            for schema in schemas:
                await catalog_session.execute(text(f"CREATE SCHEMA IF NOT EXISTS {quote_schema_identifier(schema)}"))
            await session.execute(text(_CONNECTION_DDL))
            identifier = connection_id or str(uuid.uuid4())
            if connection_id and connection_id != "configured":
                result = await session.execute(text("SELECT id FROM app.flow_connections WHERE id = :id"), {"id": connection_id})
                if result.scalar_one_or_none() is None:
                    raise HTTPException(404, "Saved connection not found")
            result = await session.execute(text("""
                INSERT INTO app.flow_connections (id, name, database_name, schemas)
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
            result = await session.execute(text("DELETE FROM app.flow_connections WHERE id = :id RETURNING id"), {"id": connection_id})
            if result.scalar_one_or_none() is None:
                raise HTTPException(404, "Saved connection not found")
        return {"deleted": True, "id": connection_id}

    async def rename_schema(self, layer: str, payload: dict[str, Any]) -> dict[str, Any]:
        schema = resolve_schema(layer)
        target = resolve_schema(payload.get("name") or schema)
        if not any(key in payload for key in ("name", "alias")):
            _bad("Provide a schema name or alias")
        alias = payload.get("alias")
        if alias is not None and (not isinstance(alias, str) or len(alias) > 200):
            _bad("Schema alias must be a string of at most 200 characters")
        async with self._session() as session, self._session(business=True, schema=schema) as catalog_session:
            await self._schema_exists(catalog_session, schema)
            if target != schema:
                await self._require_schema_write_scope(session, schema)
                await self._require_schema_write_scope(session, target)
                if await self._pipeline_schema(session, schema):
                    raise HTTPException(409, "Pipeline schemas cannot be renamed; set an alias instead")
                if schema == RESERVED_BATCH_TARGET[0] and await reserved_batch_target_exists(catalog_session):
                    raise HTTPException(409, "The schema contains a reserved synthetic batch target")
                managed = await session.execute(text("""
                    SELECT 1 FROM app.helio_objects
                    WHERE schema_name = :schema AND ddl_sql <> '' LIMIT 1
                """), {"schema": schema})
                if managed.scalar_one_or_none() is not None:
                    raise HTTPException(409, "Schemas with managed object DDL cannot be renamed; set an alias instead")
                await catalog_session.execute(text(f"ALTER SCHEMA {quote_schema_identifier(schema)} RENAME TO {quote_schema_identifier(target)}"))
                await session.execute(text("""
                    UPDATE app.helio_objects SET schema_name = :target,
                        updated_at = now() WHERE schema_name = :schema
                """), {"schema": schema, "target": target})
                for side in ("source", "target"):
                    await session.execute(text(f"""
                        UPDATE app.helio_object_relations SET {side}_schema = :target,
                            updated_at = now() WHERE {side}_schema = :schema
                    """), {"schema": schema, "target": target})
                if await self._exists(session, "flow_publications"):
                    await session.execute(text("UPDATE app.flow_publications SET schema_name = :target WHERE schema_name = :schema"), {"target": target, "schema": schema})
                if await self._exists(session, "flow_handler_drafts"):
                    await session.execute(text("UPDATE app.flow_handler_drafts SET schema_name = :target WHERE schema_name = :schema"), {"target": target, "schema": schema})
                if await self._exists(session, "flow_connections"):
                    await session.execute(text("""
                        UPDATE app.flow_connections SET schemas = (
                            SELECT jsonb_agg(CASE WHEN value = :schema THEN :target ELSE value END)
                            FROM jsonb_array_elements_text(schemas)
                        ) WHERE schemas @> CAST(:old AS jsonb)
                    """), {"schema": schema, "target": target, "old": _json([schema])})
            if alias is not None:
                await catalog_session.execute(text(f"COMMENT ON SCHEMA {quote_schema_identifier(target)} IS {_literal(alias)}".replace(":", r"\:")))
        return {"key": target, "name": target, "alias": alias}

    async def delete_schema(self, layer: str) -> dict[str, Any]:
        schema = resolve_schema(layer)
        async with self._session() as session, self._session(business=True, schema=schema) as catalog_session:
            await self._schema_exists(catalog_session, schema)
            await self._require_schema_write_scope(session, schema)
            if await self._pipeline_schema(session, schema):
                raise HTTPException(409, "Pipeline schemas cannot be deleted")
            if schema == RESERVED_BATCH_TARGET[0] and await reserved_batch_target_exists(catalog_session):
                raise HTTPException(409, "The schema contains a reserved synthetic batch target")
            await catalog_session.execute(text(f"DROP SCHEMA {quote_schema_identifier(schema)} RESTRICT"))
            await session.execute(text("DELETE FROM app.helio_object_relations WHERE source_schema = :schema OR target_schema = :schema"), {"schema": schema})
            await session.execute(text("DELETE FROM app.helio_objects WHERE schema_name = :schema"), {"schema": schema})
            if await self._exists(session, "flow_publications"):
                await session.execute(text("DELETE FROM app.flow_publications WHERE schema_name = :schema"), {"schema": schema})
            if await self._exists(session, "flow_handler_drafts"):
                await session.execute(text("DELETE FROM app.flow_handler_drafts WHERE schema_name = :schema"), {"schema": schema})
            if await self._exists(session, "flow_connections"):
                await session.execute(text("UPDATE app.flow_connections SET schemas = schemas - :schema"), {"schema": schema})
        return {"deleted": True, "name": schema}

    @staticmethod
    async def _pipeline_schema(session, schema: str) -> bool:
        result = await session.execute(text("""SELECT EXISTS (
            SELECT 1 FROM app.helio_objects
            WHERE schema_name = :schema AND is_active AND layer = ANY(CAST(:layers AS text[]))
        )"""), {"schema": schema, "layers": _PIPELINE_LAYERS})
        return bool(result.scalar_one())

    async def create_table(self, layer: str, payload: dict[str, Any]) -> dict[str, Any]:
        schema = resolve_schema(layer)
        require_ordinary_table(schema, payload.get("name"))
        if (schema, payload.get("name")) == (INDEX_SANDBOX_SCHEMA, INDEX_SANDBOX_TABLE):
            raise HTTPException(404, "Table does not exist in this schema")
        data_type = payload.get("dataType")
        if not isinstance(data_type, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", data_type):
            _bad("Data type must be a lowercase identifier of 1 to 63 characters")
        logical_layer = payload.get("logicalLayer", "other")
        if not isinstance(logical_layer, str) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", logical_layer):
            _bad("Logical layer must be a lowercase identifier")
        table_role = payload.get("tableRole", "current")
        if table_role not in ("current", "history"):
            _bad("Table role must be current or history")
        connection_id = payload.get("connectionId")
        if connection_id is not None:
            try:
                connection_id = str(uuid.UUID(connection_id))
            except (AttributeError, TypeError, ValueError):
                _bad("Select a valid data connection")
        statements = build_table_statements(schema, payload)
        relations = table_relations(schema, payload)
        connection, _ = await self._layers.connection_scope(connection_id=connection_id, logical_layer=logical_layer)
        connection_id = str(connection["id"])
        async with self._session() as session:
            binding = await session.scalar(text("""SELECT connection_id::text FROM app.etl_layer_connections
                WHERE layer = :layer FOR SHARE
            """), {"layer": logical_layer})
            if binding != connection_id:
                raise HTTPException(409, "The layer data connection changed. Reload the schema and try again.")
            async with self._session(business=True, logical_layer=logical_layer, connection_id=connection_id) as catalog_session:
                await self._schema_exists(catalog_session, schema)
                for statement in statements:
                    await catalog_session.execute(text(statement.replace(":", r"\:")))
                await sync_object_metadata(session, schema, payload["name"], catalog_session=catalog_session,
                    layer=logical_layer, table_role=table_role, data_type=data_type, data_type_source="registration",
                    provenance="flow:create_table", description=payload.get("description") or (payload.get("options") or {}).get("comment"),
                    ddl_sql=";\n".join(statements) + ";\n")
                if relations:
                    await self._create_relations(session, relations, catalog_session=catalog_session)
        return await self._layers.get_table(schema, payload["name"], connection_id=connection_id)

    async def _create_relations(self, session, rows: list[dict[str, Any]], *, catalog_session) -> None:
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('app.helio_object_relations'))"))
        targets: dict[tuple[str, str], set[str]] = {}
        endpoints = {(row[f"{side}_schema"], row[f"{side}_table"])
                     for row in rows for side in ("source", "target")}
        for schema, table in endpoints:
            count = await session.scalar(text("""SELECT count(*) FROM app.helio_objects
                WHERE schema_name = :schema AND table_name = :table AND is_active
            """), {"schema": schema, "table": table})
            if count > 1:
                raise HTTPException(409, "The selected relationship endpoint has conflicting Ontology objects")
        for row in rows:
            target = (row["target_schema"], row["target_table"])
            require_ordinary_table(*target)
            if target not in targets:
                result = await session.execute(text("""SELECT p.name
                    FROM app.helio_objects o
                    JOIN app.helio_object_properties p ON p.object_id = o.id
                    WHERE o.is_active AND o.schema_name = :schema AND o.table_name = :table
                      AND (CAST(:layers AS text[]) IS NULL OR o.layer = ANY(CAST(:layers AS text[])))
                """), {"schema": target[0], "table": target[1], "layers": catalog_logical_layers.get()})
                registered = {item["name"] for item in result.mappings()}
                physical = await catalog_session.execute(text(f"""SELECT a.attname AS name
                    FROM pg_namespace n JOIN pg_class c ON c.relnamespace=n.oid
                    JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
                    WHERE n.nspname=:schema AND c.relname=:table AND {ordinary_table_sql()}
                """), {"schema": target[0], "table": target[1]})
                targets[target] = registered & {item["name"] for item in physical.mappings()}
            if not targets[target] or not set(row["target_fields"]).issubset(targets[target]):
                _bad("Choose an active ontology target object and its registered properties")
        source = rows[0]
        result = await session.execute(text("""SELECT name, source_schema, source_table, source_fields AS columns,
                target_schema, target_table, target_fields AS referenced_columns
            FROM app.helio_object_relations
            WHERE name = ANY(CAST(:names AS text[]))
                OR (source_schema = :schema AND source_table = :table)
                OR (target_schema = :schema AND target_table = :table)
        """), {"names": [row["name"] for row in rows], "schema": source["source_schema"], "table": source["source_table"]})
        existing = list(result.mappings())
        names = {row["name"] for row in existing}
        mappings = {relation_mapping(row) for row in existing}
        for row in rows:
            mapping = relation_mapping({**row, "columns": row["source_fields"], "referenced_columns": row["target_fields"]})
            if row["name"] in names or mapping in mappings:
                raise HTTPException(409, "Relation name or property mapping already exists")
            await session.execute(text("""INSERT INTO app.helio_object_relations
                (name, source_schema, source_table, source_fields, target_schema, target_table, target_fields,
                 relationship_kind, cardinality, description, provenance, enabled)
                VALUES (:name, :source_schema, :source_table, CAST(:source_fields AS text[]),
                    :target_schema, :target_table, CAST(:target_fields AS text[]),
                    :relationship_kind, :cardinality, :description, :provenance, :enabled)
            """), row)

    async def set_object_visibility(self, layer: str, table: str, visibility: str, actor: str) -> dict[str, Any]:
        schema = resolve_schema(layer)
        quote_identifier(table)
        if visibility not in {"normal", "prominent", "hidden"}:
            _bad("Invalid object type visibility")
        async with self._session() as session:
            await EtlLayers._require_ontology(session)
            metadata = await self._ontology_object(session, schema, table)
            await session.execute(text("""UPDATE app.helio_objects
                SET visibility = :visibility, updated_by = :actor, updated_at = now()
                WHERE id = :id"""), {"visibility": visibility, "actor": actor, "id": metadata["id"]})
        return {"schema": schema, "table": table, "visibility": visibility}

    async def _ontology_object(self, session, schema: str, table: str, *, allow_missing=False):
        require_ordinary_table(schema, table)
        records = (await session.execute(text("""SELECT id FROM app.helio_objects
            WHERE schema_name = :schema AND table_name = :table AND is_active FOR UPDATE
        """), {"schema": schema, "table": table})).mappings().all()
        if not records:
            if allow_missing:
                return None
            raise HTTPException(404, "Table does not exist in this schema")
        if len(records) != 1:
            raise HTTPException(409, "The selected physical table has conflicting Ontology objects")
        return records[0]

    @staticmethod
    def _favorite_identity(layer: str, table: str) -> str:
        schema = resolve_schema(layer)
        require_ordinary_table(schema, table)
        quote_identifier(table)
        return schema

    @staticmethod
    async def _favorite_ids(session, user_id: str) -> list[str]:
        rows = await session.execute(text("""SELECT o.schema_name || '.' || o.table_name AS id
            FROM app.helio_object_favorites f JOIN app.helio_objects o ON o.id = f.object_id
            WHERE f.user_id = CAST(:user_id AS uuid) AND o.is_active
              AND o.schema_name NOT IN (:push_schema, :handoff_schema, :intent_schema)
              AND NOT (o.schema_name = :batch_schema AND o.table_name IN (:batch_target, :batch_range_target))
            ORDER BY o.schema_name, o.table_name"""),
            {"user_id": user_id, "batch_schema": RESERVED_BATCH_TARGET[0], "batch_target": RESERVED_BATCH_TARGET[1],
             "batch_range_target": BATCH_RANGE_TARGET, "push_schema": PUSH_APPLY_SCHEMA, "handoff_schema": HANDOFF_PRIVATE_SCHEMA,
             "intent_schema": SNAPSHOT_INTENT_PRIVATE_SCHEMA})
        return [row["id"] for row in rows.mappings()]

    async def object_favorites(self, user_id: str) -> dict[str, Any]:
        async with self._session() as session:
            await EtlLayers._require_ontology(session)
            return {"ids": await self._favorite_ids(session, user_id)}

    async def set_object_favorite(self, layer: str, table: str, favorite: bool, user_id: str) -> dict[str, Any]:
        schema = self._favorite_identity(layer, table)
        async with self._session() as session:
            await EtlLayers._require_ontology(session)
            metadata = await self._ontology_object(session, schema, table, allow_missing=not favorite)
            if favorite:
                await session.execute(text("""INSERT INTO app.helio_object_favorites (user_id, object_id)
                    VALUES (CAST(:user_id AS uuid), :object_id) ON CONFLICT (user_id, object_id) DO NOTHING"""),
                    {"user_id": user_id, "object_id": metadata["id"]})
            elif metadata:
                await session.execute(text("""DELETE FROM app.helio_object_favorites
                    WHERE user_id = CAST(:user_id AS uuid) AND object_id = :object_id"""),
                    {"user_id": user_id, "object_id": metadata["id"]})
        return {"id": f"{schema}.{table}", "favorite": favorite}

    async def import_object_favorites(self, ids: list[str], user_id: str) -> dict[str, Any]:
        identities = []
        if len(ids) > 200:
            _bad("At most 200 favorite identifiers are supported")
        for identifier in sorted(set(ids)):
            parts = identifier.rsplit(".", 1)
            try:
                if len(parts) != 2:
                    _bad("Invalid favorite object identifier")
                schema = self._favorite_identity(*parts)
            except HTTPException:
                _bad("Invalid favorite object identifier")
            identities.append((schema, parts[1]))
        async with self._session() as session:
            await EtlLayers._require_ontology(session)
            object_ids = []
            skipped = 0
            for schema, table in identities:
                metadata = await self._ontology_object(session, schema, table, allow_missing=True)
                if metadata:
                    object_ids.append(metadata["id"])
                else:
                    skipped += 1
            imported = 0
            if object_ids:
                rows = await session.execute(text("""INSERT INTO app.helio_object_favorites (user_id, object_id)
                    SELECT CAST(:user_id AS uuid), value FROM unnest(CAST(:object_ids AS bigint[])) AS wanted(value)
                    WHERE TRUE
                    ON CONFLICT (user_id, object_id) DO NOTHING RETURNING object_id"""),
                    {"user_id": user_id, "object_ids": object_ids})
                imported = len(list(rows.mappings()))
            return {"ids": await self._favorite_ids(session, user_id), "imported": imported, "skipped": skipped}

    @asynccontextmanager
    async def _row_write_sessions(self, schema: str, table: str, connection_id: str):
        # The business commit must finish before a concurrent layer rebind can proceed.
        async with self._session() as session:
            await session.execute(text("SET LOCAL lock_timeout='2s'"))
            matches = (await session.execute(text("""
                SELECT s.updated_at FROM app.helio_objects o
                JOIN app.etl_layer_connections b ON b.layer = o.layer
                JOIN app.helio_data_connections s ON s.id = b.connection_id
                WHERE s.paused_at IS NULL AND o.is_active AND o.schema_name = :schema AND o.table_name = :table
                  AND b.connection_id = CAST(:connection_id AS uuid)
                FOR SHARE OF o, b, s
            """), {"schema": schema, "table": table, "connection_id": connection_id})).scalars().all()
            if len(matches) != 1:
                raise HTTPException(409, "The row's data connection changed. Reload the preview before saving.")
            async with self._session(business=True, schema=schema, table=table, connection_id=connection_id) as catalog:
                yield session, catalog, matches[0]

    async def _write_row(self, layer: str, table: str, payload: dict[str, Any], delete: bool) -> dict[str, Any]:
        schema = resolve_schema(layer)
        require_ordinary_table(schema, table)
        if (schema, table) == (INDEX_SANDBOX_SCHEMA, INDEX_SANDBOX_TABLE):
            raise HTTPException(404, "Table does not exist in this schema")
        version = payload.get("expectedVersion")
        token_pattern = r"([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}):[0-9a-f]{64}"
        match = re.fullmatch(token_pattern, version) if isinstance(version, str) else None
        if not match:
            raise HTTPException(428, "Reload the row to obtain its current data connection and version")
        connection_id = match[1]
        async with self._row_write_sessions(schema, table, connection_id) as (session, catalog_session, revision):
            if delete and not await allows_physical_delete(schema, table, connection_id=connection_id):
                raise HTTPException(409, "CDC current tables require business soft deletion; physical deletion propagation is unavailable")
            qualified = f"{quote_schema_identifier(schema)}.{quote_identifier(table)}"
            columns = await self._layers.columns(schema, table, connection_id=connection_id, include_hidden=True)
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
            predicate += f" AND ({row_version_sql(connection_id, revision)}) = :version"
            source = f"jsonb_populate_record(NULL::{qualified}, CAST(:key AS jsonb)) AS k"
            if delete:
                sql = f"DELETE FROM {qualified} AS t USING {source} WHERE {predicate} RETURNING to_jsonb(t) AS row"
            else:
                assignments = ", ".join(f"{quote_identifier(name)} = v.{quote_identifier(name)}" for name in changes)
                sql = f"UPDATE {qualified} AS t SET {assignments} FROM {source}, jsonb_populate_record(NULL::{qualified}, CAST(:changes AS jsonb)) AS v WHERE {predicate} RETURNING to_jsonb(t) AS row"
            await catalog_session.execute(text("SET LOCAL lock_timeout='2s'"))
            if delete or set(changes).intersection(primary):
                from app.web.services.ontology_object_links import protect_linked_row

                await protect_linked_row(session, schema, table, key, catalog_session=catalog_session)
            result = await catalog_session.execute(text(sql), {"key": _json(key), "changes": _json(changes), "version": version})
            rows = list(result.mappings())
            if not rows:
                raise HTTPException(409, {"code": "row_version_conflict",
                                          "message": "Row changed or was deleted. Reload the preview before saving."})
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
        token = record.pop("token")
        key_status = "active"
        if token.startswith("pending:"):
            key_status = "unissued"
        elif token.startswith("revoked:"):
            key_status = "revoked"
        return {
            **scope,
            **record,
            "keyStatus": key_status,
            "url": f"{web_settings.API_V1_STR}/etl/published/{record['id']}/data",
        }

    async def publications(self, layer: str, table: str) -> list[dict[str, Any]]:
        schema = resolve_schema(layer)
        require_ordinary_table(schema, table)
        await self._layers.get_table(schema, table)
        async with self._session() as session:
            if not await self._exists(session, "flow_publications"):
                return []
            result = await session.execute(text('SELECT id, token, valid_from AS "validFrom", valid_until AS "validUntil", scope FROM app.flow_publications WHERE schema_name = :schema AND table_name = :table ORDER BY created_at'), {"schema": schema, "table": table})
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
        require_ordinary_table(schema, table)
        publication_name = _publication_name(payload["name"]) if "name" in payload else None
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
            if scope[name] and column not in known and not (name == "filterSource" and "source" in known):
                _bad(f"Publication range requires column {column}")
        # Validate the stored query through the same path that will serve the token.
        await self._layers.data(schema, table, {"limit": 1}, scope=scope)
        if publication_name is not None:
            scope["name"] = publication_name
        async with self._session() as session:
            await session.execute(text(_PUBLICATION_DDL))
            params = {"id": publication_id or str(uuid.uuid4()), "schema": schema, "table": table, "start": start, "end": end, "scope": _json(scope)}
            if publication_id:
                # Keep the current name when the client only saves range and validity.
                sql = '''UPDATE app.flow_publications SET valid_from = :start, valid_until = :end,
                            scope = jsonb_strip_nulls(jsonb_build_object('name', scope->'name')) || CAST(:scope AS jsonb)
                         WHERE id = :id AND schema_name = :schema AND table_name = :table
                         RETURNING id, token, valid_from AS "validFrom", valid_until AS "validUntil", scope'''
            else:
                params["token"] = "pending:" + params["id"]
                sql = 'INSERT INTO app.flow_publications (id, token, schema_name, table_name, valid_from, valid_until, scope) VALUES (:id, :token, :schema, :table, :start, :end, CAST(:scope AS jsonb)) RETURNING id, token, valid_from AS "validFrom", valid_until AS "validUntil", scope'
            result = await session.execute(text(sql), params)
            row = result.mappings().one_or_none()
            if row is None:
                raise HTTPException(404, "Publication not found")
            return self._publication(row)

    async def rename_publication(self, layer: str, table: str, publication_id: str, value: Any) -> dict[str, Any]:
        schema = resolve_schema(layer)
        require_ordinary_table(schema, table)
        name = _publication_name(value)
        async with self._session() as session:
            if not await self._exists(session, "flow_publications"):
                raise HTTPException(404, "Publication not found")
            result = await session.execute(
                text('''UPDATE app.flow_publications SET scope = jsonb_set(scope, '{name}', to_jsonb(CAST(:name AS text)))
                        WHERE id = :id AND schema_name = :schema AND table_name = :table
                        RETURNING id, token, valid_from AS "validFrom", valid_until AS "validUntil", scope'''),
                {"id": publication_id, "schema": schema, "table": table, "name": name},
            )
            row = result.mappings().one_or_none()
            if row is None:
                raise HTTPException(404, "Publication not found")
            return self._publication(row)

    async def delete_publication(self, layer: str, table: str, publication_id: str) -> dict[str, Any]:
        schema = resolve_schema(layer)
        require_ordinary_table(schema, table)
        async with self._session() as session:
            if not await self._exists(session, "flow_publications"):
                raise HTTPException(404, "Publication not found")
            result = await session.execute(text("DELETE FROM app.flow_publications WHERE id = :id AND schema_name = :schema AND table_name = :table RETURNING id"), {"id": publication_id, "schema": schema, "table": table})
            if result.scalar_one_or_none() is None:
                raise HTTPException(404, "Publication not found")
        return {"deleted": True, "id": publication_id}

    async def publication_keys(self, search: str, limit: int, offset: int) -> dict[str, Any]:
        async with self._session() as session:
            if not await self._exists(session, "flow_publications"):
                return {"items": [], "hasMore": False}
            result = await session.execute(
                text(
                    """
                SELECT id, schema_name AS schema, table_name AS "table", valid_from AS "validFrom",
                       valid_until AS "validUntil",
                       CASE WHEN token LIKE 'pending:%' THEN 'unissued' WHEN token LIKE 'revoked:%'
                            THEN 'revoked' ELSE 'active' END AS "keyStatus"
                FROM app.flow_publications
                WHERE strpos(lower(schema_name || '.' || table_name || ' ' || id), lower(:q)) > 0
                ORDER BY created_at DESC, id LIMIT :lim OFFSET :off"""
                ),
                {"q": search, "lim": limit + 1, "off": offset},
            )
            rows = list(result.mappings())
            return {
                "items": [
                    {**r, "url": f"{web_settings.API_V1_STR}/etl/published/{r['id']}/data"} for r in rows[:limit]
                ],
                "hasMore": len(rows) > limit,
            }

    async def change_publication_key(self, publication_id: str, *, revoke: bool) -> dict[str, Any]:
        key = None
        stored = "revoked:" + publication_id
        if not revoke:
            key = "pk_live_" + secrets.token_urlsafe(32)
            stored = "sha256:" + hashlib.sha256(key.encode()).hexdigest()
        async with self._session() as session:
            if not await self._exists(session, "flow_publications"):
                raise HTTPException(404, "Publication not found")
            result = await session.execute(
                text("""UPDATE app.flow_publications SET token = :stored WHERE id = :id
                        RETURNING schema_name, table_name"""),
                {"id": publication_id, "stored": stored},
            )
            row = result.mappings().one_or_none()
            if row is None:
                raise HTTPException(404, "Publication not found")
            require_ordinary_table(row["schema_name"], row["table_name"])
        return {
            "id": publication_id,
            "keyStatus": "revoked" if revoke else "active",
            **({"key": key} if key else {}),
        }

    async def published_data(
        self, token: str, payload: dict[str, Any], *, publication_id: str | None = None
    ) -> dict[str, Any]:
        if not token or len(token) > 256 or token.startswith(("sha256:", "pending:", "revoked:")):
            raise HTTPException(404, "Publication not found or outside its validity window")
        args = {"token": token}
        predicate = "token = :token"
        if publication_id is not None:
            args.update(id=publication_id, digest="sha256:" + hashlib.sha256(token.encode()).hexdigest())
            predicate = "id = :id AND token IN (:digest, :token)"
        # A rename does not change the data grant or invalidate an in-flight read.
        query = text(
            "SELECT schema_name, table_name, scope - 'name' AS scope FROM app.flow_publications WHERE "
            + predicate
            + " AND valid_from <= clock_timestamp() AND valid_until > clock_timestamp()"
        )
        async with self._session() as session:
            if not await self._exists(session, "flow_publications"):
                raise HTTPException(404, "Publication not found")
            result = await session.execute(query, args)
            row = result.mappings().one_or_none()
            if row is None:
                raise HTTPException(404, "Publication not found or outside its validity window")
            record = dict(row)
            require_ordinary_table(record["schema_name"], record["table_name"])
        consumer = {name: payload[name] for name in ("limit", "offset", "filters", "search") if name in payload}
        data = await self._layers.data(
            record["schema_name"], record["table_name"], consumer, scope=record["scope"]
        )
        # Prevent a scope edit, expiry or revocation during the warehouse read from releasing that result.
        async with self._session() as session:
            latest = (await session.execute(query, args)).mappings().one_or_none()
            if latest is None or dict(latest) != record:
                raise HTTPException(404, "Publication not found or outside its validity window")
        return data


@lru_cache
def get_etl_workspace() -> EtlWorkspace:
    return EtlWorkspace()
