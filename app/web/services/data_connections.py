"""Real, read-only external source probes and encrypted source persistence."""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
import importlib
import json
import ssl
from time import monotonic
from typing import Any
import uuid

from anyio import fail_after, to_thread
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import HTTPException
import httpx
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.storage.postgres_client import get_pg_client
from app.web.api.models.batch_syncs import INCREMENTAL_TARGET, TARGET_SCHEMA as BATCH_TARGET_SCHEMA
from app.web.api.models.data_connections import (
    DataConnectionPauseChange, DataConnectionPayload, SourceCatalog, SourceColumn, SourceColumns, SourceRelation,
)
from app.web.api.models.push_inbox_apply import PUSH_APPLY_SCHEMA
from app.web.services.pipeline_handoff_scope import HANDOFF_PRIVATE_SCHEMA
from app.web.core.config import settings


CONNECTORS = (
    {"engine": "postgresql", "name": "PostgreSQL", "category": "relational", "port": 5432, "driver": "asyncpg", "description": "Connect a PostgreSQL database."},
    {"engine": "mysql", "name": "MySQL", "category": "relational", "port": 3306, "driver": "pymysql", "description": "Connect a MySQL database."},
    {"engine": "oracle", "name": "Oracle", "category": "relational", "port": 1521, "driver": "oracledb", "description": "Oracle Database 12.1 or later, using Thin mode."},
    {"engine": "dameng", "name": "Dameng", "category": "relational", "port": 5236, "driver": "dmPython", "description": "Requires the vendor dmPython driver and matching DPI libraries."},
    {"engine": "elasticsearch", "name": "Elasticsearch", "category": "non_relational", "port": 9200, "driver": "httpx", "description": "Connect a search cluster using its REST API."},
    {"engine": "milvus", "name": "Milvus", "category": "vector", "port": 19530, "driver": "pymilvus", "description": "Connect a Milvus vector database."},
)
_PUBLIC_COLUMNS = "id, name, engine, config, created_by, created_at, updated_at, tested_at"
_TIMEOUT = 5
_CATALOG_TIMEOUT = 8
_CATALOG_SCHEMA_LIMIT = 100
_CATALOG_COLUMN_LIMIT = 200
_CATALOG_KINDS = {
    "r": "table", "p": "partitioned_table", "v": "view", "m": "materialized_view", "f": "foreign_table",
}
_CATALOG_SCHEMA_SCOPE = f"""
    left(n.nspname::text, 3) <> 'pg_' AND n.nspname <> 'information_schema'
    AND n.nspname NOT IN ('batch_sync_identity', 'batch_sync_stream', 'ts_cdc_sandbox', '{PUSH_APPLY_SCHEMA}', '{HANDOFF_PRIVATE_SCHEMA}')
    AND has_schema_privilege(n.oid, 'USAGE')
"""
_CATALOG_RELATION_SCOPE = f"""
    {_CATALOG_SCHEMA_SCOPE}
    AND NOT (n.nspname = '{BATCH_TARGET_SCHEMA}' AND c.relname = '{INCREMENTAL_TARGET}')
    AND CASE WHEN c.relkind IN ('r','p','v','m','f')
        THEN has_table_privilege(c.oid, 'SELECT') OR has_any_column_privilege(c.oid, 'SELECT')
        ELSE false END
"""


def _driver(engine: str):
    item = next(connector for connector in CONNECTORS if connector["engine"] == engine)
    try:
        return importlib.import_module(item["driver"])
    except (ImportError, OSError) as from_error:
        message = ("Install the vendor dmPython driver and matching Dameng DPI native libraries in the API image."
                   if engine == "dameng" else f"The {item['name']} driver is not installed in the API image.")
        raise HTTPException(503, message) from from_error


def connector_types() -> list[dict[str, Any]]:
    result = []
    for connector in CONNECTORS:
        item = {key: value for key, value in connector.items() if key != "driver"}
        try:
            _driver(connector["engine"])
            item.update(driverAvailable=True, prerequisite="", catalogAvailable=connector["engine"] == "postgresql")
        except HTTPException as failure:
            item.update(driverAvailable=False, prerequisite=failure.detail, catalogAvailable=False)
        result.append(item)
    return result


def credential_cipher() -> Fernet:
    secret = settings.SECRET_KEY
    if "SECRET_KEY" not in settings.model_fields_set or not secret or secret.lower() == "changethis":
        raise HTTPException(503, "A stable SECRET_KEY must be explicitly configured before storing source credentials")
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=b"ontology-data-connections:v1").derive(secret.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def _secrets(payload: DataConnectionPayload) -> dict[str, str]:
    return {key: value.get_secret_value() for key, value in payload.credentials if value is not None}


def paused_at_sql(alias: str) -> str:
    """Read lifecycle state from either an upgraded or independently installed legacy registry."""
    return f"(pg_catalog.to_jsonb({alias})->>'paused_at')::timestamptz"


_PAUSE_INSTALLED = text("""SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_attribute
    WHERE attrelid = pg_catalog.to_regclass('public.ontology_data_connections')
      AND attname = 'paused_at' AND attnum > 0 AND NOT attisdropped)""")


def source_pause_enabled() -> bool:
    return (getattr(settings, "ONTOLOGY_POSTGRES_SOURCE_PAUSE_ENABLED", False)
            and settings.ENVIRONMENT == "local" and "ENVIRONMENT" in settings.model_fields_set)


class SavedSourcePaused(HTTPException):
    def __init__(self):
        super().__init__(409, {"code": "source_paused", "message": "This saved PostgreSQL source is paused"})


def _public(row, *, pause_available: bool = False) -> dict[str, Any]:
    data = dict(row)
    catalog_available = False
    paused = data.get("paused_at")
    if data["engine"] == "postgresql" and paused is None:
        try:
            _driver("postgresql")
            catalog_available = True
        except HTTPException:
            pass  # Saved source remains visible when its optional driver is missing.
    result = {"id": str(data["id"]), "name": data["name"], "engine": data["engine"],
            "config": json.loads(data["config"]) if isinstance(data["config"], str) else data["config"],
            "credentialsConfigured": True, "catalogAvailable": catalog_available, "createdBy": data["created_by"],
            "createdAt": data["created_at"].isoformat(), "updatedAt": data["updated_at"].isoformat(),
            "testedAt": data["tested_at"].isoformat() if data["tested_at"] is not None else None}
    if paused is not None or pause_available and data["engine"] == "postgresql":
        result.update(pausedAt=paused.isoformat() if paused is not None else None, pauseAvailable=pause_available)
    return result


async def _probe(payload: DataConnectionPayload, credentials: dict[str, str]) -> None:
    driver = _driver(payload.engine)
    config = payload.config
    tls = ssl.create_default_context() if config.tls else None
    if payload.engine == "postgresql":
        connection = await driver.connect(host=config.host, port=config.port, database=config.database,
                                          user=config.username, password=credentials["password"],
                                          ssl=tls if tls else False, timeout=_TIMEOUT, command_timeout=_TIMEOUT)
        try:
            await connection.fetchval("SELECT 1")
        finally:
            try:
                await connection.close(timeout=_TIMEOUT)
            except BaseException:
                connection.terminate()
                raise
    elif payload.engine == "elasticsearch":
        host = f"[{config.host}]" if ":" in config.host else config.host
        url = f"{'https' if config.tls else 'http'}://{host}:{config.port}/"
        auth = httpx.BasicAuth(config.username, credentials["password"]) if config.auth == "password" else None
        headers = {"Authorization": f"ApiKey {credentials['apiKey']}"} if config.auth == "api_key" else {}
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False, follow_redirects=False) as client:
            async with client.stream("GET", url, auth=auth, headers=headers) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 65_536:
                        raise ValueError("Invalid Elasticsearch information response")
                data = json.loads(body)
                if not isinstance(data, dict) or not data.get("cluster_name") or not isinstance(data.get("version"), dict) or not data["version"].get("number"):
                    raise ValueError("Endpoint is not an Elasticsearch cluster")
    elif payload.engine == "oracle":
        params = driver.ConnectParams(host=config.host, port=config.port, service_name=config.database,
                                      protocol="tcps" if config.tls else "tcp", tcp_connect_timeout=_TIMEOUT,
                                      retry_count=0, ssl_server_dn_match=True, ssl_context=tls)
        async with await driver.connect_async(user=config.username, password=credentials["password"], params=params) as connection:
            connection.call_timeout = _TIMEOUT * 1000
            with connection.cursor() as cursor:
                await cursor.execute("SELECT 1 FROM DUAL")
                await cursor.fetchone()
    else:
        # These drivers expose synchronous clients. Their own network/query deadlines
        # remain active even if the awaiting request is cancelled.
        def synchronous_probe():
            if payload.engine == "mysql":
                connection = driver.connect(host=config.host, port=config.port, database=config.database,
                                            user=config.username, password=credentials["password"],
                                            connect_timeout=_TIMEOUT, read_timeout=_TIMEOUT, write_timeout=_TIMEOUT,
                                            ssl=tls, ssl_disabled=not config.tls, autocommit=False)
                try:
                    with connection.cursor() as cursor:
                        if config.tls:
                            cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                            row = cursor.fetchone()
                            if not row or not row[1]:
                                raise ssl.SSLError("The server did not negotiate TLS")
                        cursor.execute("SELECT 1")
                        cursor.fetchone()
                finally:
                    connection.close()
            elif payload.engine == "dameng":
                connection = driver.connect(server=config.host, port=config.port, user=config.username,
                                            password=credentials["password"], login_timeout=_TIMEOUT * 1000,
                                            connection_timeout=_TIMEOUT)
                try:
                    cursor = connection.cursor()
                    try:
                        cursor.execute("SELECT 1 FROM DUAL")
                        cursor.fetchone()
                    finally:
                        cursor.close()
                finally:
                    connection.close()
            else:
                host = f"[{config.host}]" if ":" in config.host else config.host
                options = {"token": credentials["token"]} if config.auth == "token" else {"user": config.username, "password": credentials["password"]}
                client = driver.MilvusClient(uri=f"{'https' if config.tls else 'http'}://{host}:{config.port}",
                                             db_name=config.database, timeout=_TIMEOUT, **options)
                try:
                    client.list_collections(timeout=_TIMEOUT)
                finally:
                    client.close()
        await to_thread.run_sync(synchronous_probe)


def _failure_message(error: Exception) -> str:
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "Connection timed out. Check the host, port and network access."
    if isinstance(error, ssl.SSLError):
        return "TLS verification failed. Check the server certificate, hostname and transport settings."
    if isinstance(error, httpx.HTTPStatusError):
        return ("Authentication or read access was rejected." if error.response.status_code in {401, 403}
                else "The server rejected the connection probe. Verify the endpoint and account permissions.")
    if (getattr(error, "sqlstate", "") or "").startswith("28") or error.__class__.__name__ in {"AuthenticationError", "PermissionDenied"}:
        return "Authentication or read access was rejected."
    return "Connection failed. Verify the address, credentials, database scope, TLS and account read permissions."


class DataConnections:
    def __init__(self):
        self._pg = get_pg_client()

    @asynccontextmanager
    async def _session(self):
        try:
            with fail_after(20):
                await self._pg.connect(max_retries=1)
                async with self._pg.session() as session:
                    await session.execute(text("SET LOCAL statement_timeout = '10s'"))
                    await session.execute(text("SET LOCAL lock_timeout = '2s'"))
                    yield session
        except HTTPException:
            raise
        except DBAPIError as error:
            code = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
            if code == "55P03":
                raise HTTPException(409, {"code": "source_busy", "message": "This saved source is busy; try again"}) from None
            if code == "42P01":
                raise HTTPException(503, "External source registry is not initialized; run tools/ontology_data_connections.py --apply") from error
            raise HTTPException(503, "External source registry operation failed") from error
        except (ConnectionError, OSError, TimeoutError) as error:
            raise HTTPException(503, "External source registry is unavailable") from error

    async def list(self):
        async with self._session() as session:
            can_pause = source_pause_enabled() and await session.scalar(_PAUSE_INSTALLED)
            rows = (await session.execute(text(f"""
                SELECT {_PUBLIC_COLUMNS}, {paused_at_sql('registry')} AS paused_at
                FROM public.ontology_data_connections registry ORDER BY lower(name), id
            """))).mappings()
            return [_public(row, pause_available=bool(can_pause)) for row in rows]

    async def _stored(self, connection_id: str, *, session=None, lock: str = ""):
        if session is None:
            # Worker and identity verification already hold the registry row lock in their outer transaction.
            async with self._session() as opened:
                return await self._stored(connection_id, session=opened)
        state = paused_at_sql("registry")
        row = (await session.execute(text(f"""
            SELECT {_PUBLIC_COLUMNS}, {state} AS paused_at,
                CASE WHEN {state} IS NULL THEN credentials_ciphertext END AS credentials_ciphertext
            FROM public.ontology_data_connections registry WHERE id=:id
            """ + lock), {"id": uuid.UUID(connection_id)})).mappings().first()
        if row is None:
            raise HTTPException(404, "Data source not found")
        if row["paused_at"] is not None:
            raise SavedSourcePaused()
        return dict(row)

    async def change_pause(self, connection_id: str, change: DataConnectionPauseChange):
        if not source_pause_enabled():
            raise HTTPException(403, "Saved PostgreSQL source pause requires explicit local opt-in")
        async with self._session() as session:
            if not await session.scalar(_PAUSE_INSTALLED):
                raise HTTPException(503, "Install the external source registry lifecycle migration first")
            params = {"id": uuid.UUID(connection_id)}
            row = (await session.execute(text(f"""
                SELECT {_PUBLIC_COLUMNS}, paused_at FROM public.ontology_data_connections
                WHERE id=:id FOR UPDATE
            """), params)).mappings().first()
            if row is None:
                raise HTTPException(404, "Data source not found")
            if row["engine"] != "postgresql":
                raise HTTPException(422, "Only saved PostgreSQL sources can be paused")
            if row["updated_at"] != change.expectedUpdatedAt:
                raise HTTPException(409, {"code": "source_revision_changed",
                                          "message": "The saved source changed; reload it before changing its pause state"})
            if (row["paused_at"] is not None) == change.paused:
                return _public(row, pause_available=True)
            updated = (await session.execute(text(f"""
                UPDATE public.ontology_data_connections
                SET paused_at=CASE WHEN :paused THEN clock_timestamp() ELSE NULL END, tested_at=NULL,
                    updated_at=greatest(clock_timestamp(), updated_at + interval '1 microsecond')
                WHERE id=:id AND updated_at=:revision RETURNING {_PUBLIC_COLUMNS}, paused_at
            """), {**params, "revision": row["updated_at"], "paused": change.paused})).mappings().one()
            return _public(updated, pause_available=True)

    async def _credentials(self, payload, stored=None):
        provided = _secrets(payload)
        if provided:
            return provided
        if stored:
            row = stored
            old_config = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
            # A saved secret belongs to exactly this connection configuration.
            # A rename or an explicit test of the saved source may reuse it.
            if row["engine"] != payload.engine or old_config != payload.config.model_dump():
                raise HTTPException(422, "Enter new credentials when changing the connection settings")
            try:
                return json.loads(credential_cipher().decrypt(row["credentials_ciphertext"].encode("ascii")))
            except (InvalidToken, ValueError, UnicodeError) as error:
                raise HTTPException(503, "Stored credentials cannot be decrypted; restore the configured SECRET_KEY or replace these credentials") from error
        raise HTTPException(422, "Enter credentials for the selected authentication method")

    async def test(self, payload: DataConnectionPayload, connection_id: str | None = None):
        if connection_id is None:
            return await self._probe_result(payload, await self._credentials(payload))
        async with self._session() as session:
            stored = await self._stored(connection_id, session=session, lock=" FOR SHARE")
            return await self._probe_result(payload, await self._credentials(payload, stored))

    async def _probe_result(self, payload, credentials):
        start = monotonic()
        try:
            with fail_after(15):
                await _probe(payload, credentials)
        except HTTPException:
            raise
        except Exception as error:
            # Driver errors may include passwords and connection strings. Never echo
            # or log the raw exception, even when the remote service rejects auth.
            return {"ok": False, "message": _failure_message(error), "latencyMs": round((monotonic() - start) * 1000)}
        return {"ok": True, "message": "Connection verified with a read-only request.",
                "latencyMs": round((monotonic() - start) * 1000), "testedAt": datetime.now(timezone.utc).isoformat()}

    async def test_saved(self, connection_id):
        async with self._session() as session:
            row = await self._stored(connection_id, session=session, lock=" FOR UPDATE")
            payload = DataConnectionPayload(name=row["name"], engine=row["engine"], config=row["config"])
            credentials = await self._credentials(payload, row)
            result = await self._probe_result(payload, credentials)
            if result["ok"]:
                await session.execute(text("UPDATE public.ontology_data_connections SET tested_at=:tested WHERE id=:id"),
                                      {"id": uuid.UUID(connection_id), "tested": datetime.fromisoformat(result["testedAt"])})
            return result

    @asynccontextmanager
    async def _postgres_catalog(self, connection_id):
        async with self._session() as session:
            row = await self._stored(connection_id, session=session, lock=" FOR SHARE")
            async with self._postgres_catalog_row(row) as connection:
                yield connection

    @asynccontextmanager
    async def _postgres_catalog_row(self, row):
        if row["engine"] != "postgresql":
            raise HTTPException(422, "Catalog exploration is supported only for saved PostgreSQL sources")
        try:
            config = row["config"]
            payload = DataConnectionPayload(name=row["name"], engine="postgresql",
                                            config=json.loads(config) if isinstance(config, str) else config)
        except (ValidationError, ValueError, TypeError):
            raise HTTPException(503, "Saved source settings are invalid; verify its connection settings") from None
        credentials = await self._credentials(payload, row)
        password = credentials.get("password") if isinstance(credentials, dict) else None
        if not isinstance(password, str) or not password:
            raise HTTPException(503, "Stored PostgreSQL credentials are unavailable")
        config = payload.config
        driver = _driver("postgresql")
        connection = None
        try:
            with fail_after(_CATALOG_TIMEOUT):
                connection = await driver.connect(
                    host=config.host, port=config.port, database=config.database, user=config.username,
                    password=password, ssl=ssl.create_default_context() if config.tls else False,
                    timeout=_TIMEOUT, command_timeout=2,
                    server_settings={"search_path": "pg_catalog", "statement_timeout": "2000",
                                     "lock_timeout": "1000", "default_transaction_read_only": "on"},
                )
                async with connection.transaction(readonly=True, isolation="repeatable_read"):
                    yield connection
        except HTTPException:
            raise
        except Exception as error:
            # Neither the upstream error text nor its traceback is an API or logging surface.
            if isinstance(error, TimeoutError) or getattr(error, "sqlstate", None) == "57014":
                raise HTTPException(504, "Catalog query timed out; check connectivity and try a narrower search") from None
            raise HTTPException(502, _failure_message(error)) from None
        finally:
            if connection is not None:
                connection.terminate()

    async def catalog(self, connection_id, *, schema: str | None, search: str, limit: int, offset: int) -> SourceCatalog:
        async with self._postgres_catalog(connection_id) as connection:
            schemas = await connection.fetch(f"""
                SELECT n.nspname::text AS name FROM pg_catalog.pg_namespace n
                WHERE {_CATALOG_SCHEMA_SCOPE}
                ORDER BY n.nspname::text COLLATE "C"
                LIMIT $1
            """, _CATALOG_SCHEMA_LIMIT + 1)
            relations = []
            if schema is not None:
                allowed = await connection.fetchval(f"""
                    SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n
                                   WHERE {_CATALOG_SCHEMA_SCOPE} AND n.nspname::text = $1)
                """, schema)
                if not allowed:
                    raise HTTPException(404, "Source schema is unavailable or not readable")
                relations = await connection.fetch(f"""
                    SELECT c.relname::text AS name, c.relkind::text AS kind
                    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                    WHERE {_CATALOG_RELATION_SCOPE} AND n.nspname::text = $1
                      AND ($2::text = '' OR position(lower($2::text) in lower(c.relname::text)) > 0)
                    ORDER BY c.relname::text COLLATE "C"
                    LIMIT $3 OFFSET $4
                """, schema, search, limit + 1, offset)
        return SourceCatalog(
            schemas=[row["name"] for row in schemas[:_CATALOG_SCHEMA_LIMIT]], schemaHasMore=len(schemas) > _CATALOG_SCHEMA_LIMIT,
            schema=schema, relations=[SourceRelation(name=row["name"], kind=_CATALOG_KINDS[row["kind"]])
                                      for row in relations[:limit]], hasMore=len(relations) > limit,
        )

    async def catalog_columns(self, connection_id, *, schema: str, table: str) -> SourceColumns:
        async with self._postgres_catalog(connection_id) as connection:
            relation = await connection.fetchval(f"""
                SELECT c.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE {_CATALOG_RELATION_SCOPE} AND n.nspname::text = $1 AND c.relname::text = $2
            """, schema, table)
            if relation is None:
                raise HTTPException(404, "Source relation is unavailable or not readable")
            columns = await connection.fetch("""
                SELECT a.attname::text AS name, format_type(a.atttypid, a.atttypmod) AS type,
                       NOT a.attnotnull AS nullable, a.attnum AS position,
                       EXISTS (SELECT 1 FROM unnest(i.indkey) WITH ORDINALITY AS pk(attnum, ord)
                               WHERE pk.attnum = a.attnum AND pk.ord <= i.indnkeyatts) AS primary_key
                FROM pg_catalog.pg_attribute a
                LEFT JOIN pg_catalog.pg_index i ON i.indrelid = a.attrelid AND i.indisprimary
                  AND i.indisvalid AND i.indisready AND i.indislive
                WHERE a.attrelid = $1::oid AND a.attnum > 0 AND NOT a.attisdropped
                  AND has_column_privilege(a.attrelid, a.attnum, 'SELECT')
                ORDER BY a.attnum
                LIMIT $2
            """, relation, _CATALOG_COLUMN_LIMIT + 1)
        return SourceColumns(schema=schema, table=table, hasMore=len(columns) > _CATALOG_COLUMN_LIMIT,
                             columns=[SourceColumn(name=row["name"], type=row["type"], nullable=row["nullable"],
                                                   primaryKey=row["primary_key"], position=row["position"])
                                      for row in columns[:_CATALOG_COLUMN_LIMIT]])

    async def save(self, payload, user_id, connection_id=None):
        if connection_id is None:
            params = await self._prepare_save(payload, user_id, uuid.uuid4(), None)
            async with self._session() as session:
                return await self._save_verified(session, params)
        async with self._session() as session:
            stored = await self._stored(connection_id, session=session, lock=" FOR UPDATE")
            params = await self._prepare_save(payload, user_id, uuid.UUID(connection_id), stored)
            return await self._save_verified(session, params)

    async def _prepare_save(self, payload, user_id, identifier, stored):
        cipher = credential_cipher()
        credentials = await self._credentials(payload, stored)
        result = await self._probe_result(payload, credentials)
        if not result["ok"]:
            raise HTTPException(422, result["message"])
        params = {"id": identifier, "name": payload.name, "engine": payload.engine,
                  "config": json.dumps(payload.config.model_dump()),
                  "credentials": (stored["credentials_ciphertext"] if stored and not _secrets(payload)
                                  else cipher.encrypt(json.dumps(credentials).encode("utf-8")).decode("ascii")),
                  "user": str(user_id), "tested": datetime.fromisoformat(result["testedAt"])}
        if stored:
            params["version"] = stored["updated_at"]
        return params

    async def _save_verified(self, session, params):
        if "version" in params:
            sql = f"""UPDATE public.ontology_data_connections SET name=:name, engine=:engine,
                config=CAST(:config AS jsonb), credentials_ciphertext=:credentials,
                updated_at=greatest(clock_timestamp(), updated_at + interval '1 microsecond'),
                tested_at=:tested WHERE id=:id AND updated_at=:version RETURNING {_PUBLIC_COLUMNS}"""
        else:
            sql = f"""INSERT INTO public.ontology_data_connections
                (id,name,engine,config,credentials_ciphertext,created_by,tested_at)
                VALUES (:id,:name,:engine,CAST(:config AS jsonb),:credentials,:user,:tested)
                RETURNING {_PUBLIC_COLUMNS}"""
        row = (await session.execute(text(sql), params)).mappings().first()
        if row is None:
            raise HTTPException(409, "The data source changed during verification; reload before saving")
        can_pause = source_pause_enabled() and await session.scalar(_PAUSE_INSTALLED)
        return _public(row, pause_available=bool(can_pause))

    async def delete(self, connection_id):
        async with self._session() as session:
            row = (await session.execute(text("DELETE FROM public.ontology_data_connections WHERE id=:id RETURNING id"), {"id": uuid.UUID(connection_id)})).first()
            if row is None:
                raise HTTPException(404, "Data source not found")
        return {"deleted": True}


@lru_cache
def get_data_connections() -> DataConnections:
    return DataConnections()
