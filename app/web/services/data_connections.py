"""Real, read-only external source probes and encrypted source persistence."""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager, closing
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
from app.web.services.source_cdc import CONNECTORS as CDC_CONNECTORS, require_unconfigured
from app.web.api.models.data_connections import (
    DataConnectionPauseChange, DataConnectionPayload, SavedSourceCapabilities, SavedSourceCapability,
    SavedSourceCapabilityReason, SourceCatalog, SourceColumn, SourceColumns, SourceReference,
    SourceReferenceDirection, SourceReferencePair, SourceReferences, SourceRelation,
)
from app.web.api.models.push_inbox_apply import PUSH_APPLY_SCHEMA
from app.web.services.batch_snapshot_range_scope import BATCH_RANGE_SOURCE_SCHEMA, BATCH_RANGE_TARGET
from app.web.services.pipeline_handoff_scope import HANDOFF_PRIVATE_SCHEMA
from app.web.services.snapshot_intent_scope import SNAPSHOT_INTENT_PRIVATE_SCHEMA
from app.web.core.config import settings


CONNECTORS = (
    {"engine": "postgresql", "name": "PostgreSQL", "category": "relational", "port": 5432, "driver": "asyncpg", "description": "Connect a PostgreSQL database."},
    {"engine": "mysql", "name": "MySQL", "category": "relational", "port": 3306, "driver": "pymysql", "description": "Connect a MySQL database."},
    {"engine": "oracle", "name": "Oracle", "category": "relational", "port": 1521, "driver": "oracledb", "description": "Oracle Database 12.1 or later, using Thin mode."},
    {"engine": "dameng", "name": "Dameng", "category": "relational", "port": 5236, "driver": "dmPython", "description": "Connect a Dameng database using the official dmPython driver."},
    {"engine": "mongodb", "name": "MongoDB", "category": "document", "port": 27017, "driver": "pymongo", "description": "Connect a MongoDB document database."},
    {"engine": "elasticsearch", "name": "Elasticsearch", "category": "search", "port": 9200, "driver": "httpx", "description": "Connect a search cluster using its REST API."},
    {"engine": "milvus", "name": "Milvus", "category": "vector", "port": 19530, "driver": "pymilvus", "description": "Connect a Milvus vector database."},
    {"engine": "s3", "name": "S3-compatible storage", "category": "object_storage", "port": 443, "driver": "boto3", "description": "Connect a bucket through an S3-compatible service endpoint."},
    {"engine": "redis", "name": "Redis", "category": "key_value", "port": 6379, "driver": "redis.asyncio", "description": "Connect a Redis database using its RESP endpoint."},
    {"engine": "clickhouse", "name": "ClickHouse", "category": "relational", "port": 8443, "driver": "httpx", "description": "Connect a ClickHouse database through its HTTP interface."},
)
_PUBLIC_COLUMNS = "id, name, engine, config, created_by, created_at, updated_at, tested_at"
_TIMEOUT = 5
_CATALOG_TIMEOUT = 8
_CATALOG_SCHEMA_LIMIT = 100
_CATALOG_COLUMN_LIMIT = 200
_CATALOG_REFERENCE_LIMIT = 12
_CATALOG_KINDS = {
    "r": "table", "p": "partitioned_table", "v": "view", "m": "materialized_view", "f": "foreign_table",
}
_CATALOG_SCHEMA_SCOPE = f"""
    left(n.nspname::text, 3) <> 'pg_' AND n.nspname <> 'information_schema'
    AND n.nspname NOT IN ('batch_sync_identity', 'batch_sync_stream', '{BATCH_RANGE_SOURCE_SCHEMA}', 'ts_cdc_sandbox', '{PUSH_APPLY_SCHEMA}', '{HANDOFF_PRIVATE_SCHEMA}', '{SNAPSHOT_INTENT_PRIVATE_SCHEMA}')
    AND has_schema_privilege(n.oid, 'USAGE')
"""
_CATALOG_RELATION_SCOPE = f"""
    {_CATALOG_SCHEMA_SCOPE}
    AND NOT (n.nspname = '{BATCH_TARGET_SCHEMA}' AND c.relname IN ('{INCREMENTAL_TARGET}', '{BATCH_RANGE_TARGET}'))
    AND CASE WHEN c.relkind IN ('r','p','v','m','f')
        THEN has_table_privilege(c.oid, 'SELECT') OR has_any_column_privilege(c.oid, 'SELECT')
        ELSE false END
"""

# References are intentionally limited to ordinary tables outside all inheritance trees.
_REFERENCE_TABLE = """
    c.relkind = 'r' AND NOT c.relispartition
    AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits edge
                    WHERE edge.inhrelid = c.oid OR edge.inhparent = c.oid)
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
        item["cdcSupported"] = connector["engine"] in CDC_CONNECTORS
        item["captureKind"] = ("dmdrs" if connector["engine"] == "dameng" else
                               "object_events" if connector["engine"] == "s3" else
                               "cdc" if connector["engine"] in CDC_CONNECTORS else None)
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


def _driver_available(engine: str) -> bool:
    if not any(connector["engine"] == engine for connector in CONNECTORS):
        return False
    try:
        _driver(engine)
        return True
    except Exception:
        # Optional compiled drivers can fail during import for reasons besides a missing module.
        # A readiness hint never propagates the install error or changes live Test semantics.
        return False


def _capability(reason: SavedSourceCapabilityReason | None = None) -> SavedSourceCapability:
    return SavedSourceCapability(status="unavailable" if reason else "available_to_try", reason=reason)


def _saved_capabilities(
    row, *, driver_available: bool, explicit_local: bool, batch_enabled: bool, numeric_loopback: bool,
    identity_registry: bool, identity_required: bool, identity_provisioned: bool,
    committed_enabled: bool, range_enabled: bool,
) -> SavedSourceCapabilities:
    postgres = row["engine"] == "postgresql"
    if row["paused_at"] is not None:
        paused = _capability("source_paused")
        return SavedSourceCapabilities(connectionTest=paused, catalog=paused, snapshot=paused,
                                       committedBatch=paused, reviewedRange=paused)

    supported = any(connector["engine"] == row["engine"] for connector in CONNECTORS)
    test = _capability("unsupported_engine" if not supported else None if driver_available else "driver_missing")
    catalog = test if postgres else _capability("unsupported_engine")
    general = catalog
    if general.status == "available_to_try":
        if not explicit_local:
            general = _capability("explicit_local_required")
        elif not batch_enabled:
            general = _capability("mode_disabled")
        elif not numeric_loopback:
            general = _capability("numeric_loopback_required")

    def managed(mode_enabled: bool | None) -> SavedSourceCapability:
        if general.status == "unavailable":
            return general
        if mode_enabled is False:
            return _capability("mode_disabled")
        if mode_enabled and not identity_required:
            return _capability("source_identity_policy_disabled")
        if identity_required and not identity_provisioned:
            return _capability("source_identity_not_configured")
        if not identity_registry:
            return _capability("source_identity_registry_unavailable")
        if not identity_required:
            return _capability("source_identity_policy_disabled" if row["has_proof"] else None)
        if not row["has_proof"]:
            return _capability("source_identity_unverified")
        return _capability(None if row["proof_matches"] else "source_identity_stale")

    return SavedSourceCapabilities(connectionTest=test, catalog=catalog, snapshot=managed(None),
                                   committedBatch=managed(committed_enabled), reviewedRange=managed(range_enabled))


def _public(row, *, pause_available: bool = False, driver_available: bool | None = None) -> dict[str, Any]:
    data = dict(row)
    catalog_available = False
    paused = data.get("paused_at")
    if data["engine"] == "postgresql" and paused is None:
        catalog_available = _driver_available("postgresql") if driver_available is None else driver_available
    result = {"id": str(data["id"]), "name": data["name"], "engine": data["engine"],
            "config": json.loads(data["config"]) if isinstance(data["config"], str) else data["config"],
            "credentialsConfigured": True, "catalogAvailable": catalog_available, "createdBy": data["created_by"],
            "createdAt": data["created_at"].isoformat(), "updatedAt": data["updated_at"].isoformat(),
            "testedAt": data["tested_at"].isoformat() if data["tested_at"] is not None else None}
    if paused is not None or pause_available and data["engine"] == "postgresql":
        result.update(pausedAt=paused.isoformat() if paused is not None else None, pauseAvailable=pause_available)
    return result


def _http_endpoint(config):
    host = f"[{config.host}]" if ":" in config.host else config.host
    return f"{'https' if config.tls else 'http'}://{host}:{config.port}"


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
    elif payload.engine in {"elasticsearch", "clickhouse"}:
        url = _http_endpoint(config) + "/"
        auth = httpx.BasicAuth(config.username, credentials["password"]) if config.auth == "password" else None
        headers = {"Authorization": f"ApiKey {credentials['apiKey']}"} if config.auth == "api_key" else {}
        params = ({"database": config.database, "query": "SELECT currentDatabase() FORMAT TabSeparatedRaw"}
                  if payload.engine == "clickhouse" else None)
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False, follow_redirects=False) as client:
            async with client.stream("GET", url, auth=auth, headers=headers, params=params) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 65_536:
                        raise ValueError("Connection probe response exceeds the size limit")
                if payload.engine == "clickhouse":
                    if body != (config.database + "\n").encode():
                        raise ValueError("ClickHouse did not confirm the selected database")
                    return
                data = json.loads(body)
                if not isinstance(data, dict) or not data.get("cluster_name") or not isinstance(data.get("version"), dict) or not data["version"].get("number"):
                    raise ValueError("Endpoint is not an Elasticsearch cluster")
    elif payload.engine == "redis":
        async with driver.Redis(host=config.host, port=config.port, db=int(config.database),
                                username=config.username, password=credentials["password"], ssl=config.tls,
                                ssl_check_hostname=True, socket_connect_timeout=_TIMEOUT, socket_timeout=_TIMEOUT) as client:
            if await client.ping() is not True:
                raise ValueError("Redis did not acknowledge PING")
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
            if payload.engine == "s3":
                from botocore.config import Config

                options = Config(signature_version="s3v4", s3={"addressing_style": config.addressingStyle or "path"}, proxies={},
                                 connect_timeout=_TIMEOUT, read_timeout=_TIMEOUT, retries={"total_max_attempts": 1})
                with closing(driver.Session().client(
                    "s3", endpoint_url=_http_endpoint(config), region_name=config.region, config=options,
                    aws_access_key_id=config.username, aws_secret_access_key=credentials["secretAccessKey"],
                )) as client:
                    result = client.list_objects_v2(Bucket=config.bucket, MaxKeys=0)
                    if result.get("Name") != config.bucket:
                        raise ValueError("S3 did not confirm the selected bucket")
            elif payload.engine == "mysql":
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
            elif payload.engine == "mongodb":
                host = f"[{config.host}]" if ":" in config.host else config.host
                with driver.MongoClient(host=host, port=config.port,
                                        username=config.username, password=credentials["password"],
                                        authSource=config.authDatabase, tls=config.tls, directConnection=True,
                                        timeoutMS=_TIMEOUT * 1000, retryReads=False, retryWrites=False,
                                        connect=False) as client:
                    # Verify the selected database's catalog permission, not just that a server answers ping.
                    client[config.database].command({
                        "listCollections": 1, "nameOnly": True, "filter": {"name": {"$in": []}},
                    })
            else:
                options = {"token": credentials["token"]} if config.auth == "token" else {"user": config.username, "password": credentials["password"]}
                client = driver.MilvusClient(uri=_http_endpoint(config),
                                             db_name=config.database, timeout=_TIMEOUT, **options)
                try:
                    client.list_collections(timeout=_TIMEOUT)
                finally:
                    client.close()
        await to_thread.run_sync(synchronous_probe)


def _failure_message(error: Exception) -> str:
    if isinstance(error, (TimeoutError, httpx.TimeoutException)) or getattr(error, "timeout", False) is True:
        return "Connection timed out. Check the host, port and network access."
    if isinstance(error, ssl.SSLError):
        return "TLS verification failed. Check the server certificate, hostname and transport settings."
    if isinstance(error, httpx.HTTPStatusError):
        return ("Authentication or read access was rejected." if error.response.status_code in {401, 403}
                else "The server rejected the connection probe. Verify the endpoint and account permissions.")
    if ((getattr(error, "sqlstate", "") or "").startswith("28")
            or error.__class__.__name__ in {"AuthenticationError", "PermissionDenied"}
            or (error.__class__.__module__ == "pymongo.errors" and getattr(error, "code", None) in {13, 18})):
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

    async def exists(self, connection_id: uuid.UUID) -> bool:
        async with self._session() as session:
            return bool(await session.scalar(text(
                "SELECT EXISTS (SELECT 1 FROM public.ontology_data_connections WHERE id = :id)"
            ), {"id": connection_id}))

    async def list(self):
        # Identity also imports the saved-connection service for its explicit external verify action.
        from app.web.services.batch_source_identity import (
            _explicit_local_batch, _local_host, expected_fingerprint, identity_required,
        )

        explicit_local = "ENVIRONMENT" in settings.model_fields_set and settings.ENVIRONMENT == "local"
        batch_enabled = _explicit_local_batch()
        required, fingerprint = identity_required(), expected_fingerprint()
        async with self._session() as session:
            can_pause = source_pause_enabled() and await session.scalar(_PAUSE_INSTALLED)
            identity_registry = bool(batch_enabled and await session.scalar(text("""
                SELECT pg_catalog.to_regclass('public.ontology_batch_source_identities') IS NOT NULL
            """)))
            join = "LEFT JOIN public.ontology_batch_source_identities proof ON proof.connection_id=registry.id" if identity_registry else ""
            proof_columns = (
                """proof.connection_id IS NOT NULL AS has_proof, COALESCE(
                    proof.is_current AND proof.source_updated_at=registry.updated_at
                    AND proof.fingerprint=:fingerprint, false) AS proof_matches"""
                if identity_registry else "false AS has_proof, false AS proof_matches"
            )
            columns = ", ".join(f"registry.{column}" for column in _PUBLIC_COLUMNS.split(", "))
            rows = (await session.execute(text(f"""
                SELECT {columns}, {paused_at_sql('registry')} AS paused_at,
                    registry.config->>'host' AS source_host, {proof_columns}
                FROM public.ontology_data_connections registry {join}
                ORDER BY lower(registry.name), registry.id
            """), {"fingerprint": fingerprint} if identity_registry else {})).mappings()

            drivers: dict[str, bool] = {}
            result = []
            for row in rows:
                engine = row["engine"]
                if row["paused_at"] is None and engine not in drivers:
                    drivers[engine] = _driver_available(engine)
                driver = drivers.get(engine, False)
                item = _public(row, pause_available=bool(can_pause), driver_available=driver)
                item["capabilities"] = _saved_capabilities(
                    row, driver_available=driver, explicit_local=explicit_local, batch_enabled=batch_enabled,
                    numeric_loopback=_local_host(row["source_host"]), identity_registry=identity_registry,
                    identity_required=required, identity_provisioned=fingerprint is not None,
                    committed_enabled=bool(batch_enabled and settings.BATCH_SYNC_INCREMENTAL_ENABLED),
                    range_enabled=bool(batch_enabled and settings.BATCH_SYNC_RANGE_SNAPSHOT_ENABLED),
                ).model_dump(exclude_none=True)
                result.append(item)
            return result

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
            await require_unconfigured(connection_id, row["engine"], registry=self, row=row, session=session)
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

    @staticmethod
    def _require_draft_revision(payload: DataConnectionPayload, stored, expected_updated_at: datetime | None) -> None:
        if "postgresql" in (stored["engine"], payload.engine) and (
            expected_updated_at is None or stored["updated_at"] != expected_updated_at
        ):
            raise HTTPException(409, {
                "code": "source_revision_changed",
                "message": "The saved source changed; reload it before testing or saving this draft",
            })

    async def test(
        self, payload: DataConnectionPayload, connection_id: str | None = None,
        *, expected_updated_at: datetime | None = None,
    ):
        if connection_id is None:
            return await self._probe_result(payload, await self._credentials(payload))
        async with self._session() as session:
            stored = await self._stored(connection_id, session=session, lock=" FOR SHARE")
            self._require_draft_revision(payload, stored, expected_updated_at)
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
    async def _postgres_catalog(self, connection_id, *, expected_updated_at: datetime | None = None):
        async with self._session() as session:
            row = await self._stored(connection_id, session=session, lock=" FOR SHARE")
            if expected_updated_at is not None and row["updated_at"] != expected_updated_at:
                raise HTTPException(409, {"code": "source_revision_changed",
                                          "message": "The saved source changed; reload it before browsing references"})
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
            relation = await connection.fetchrow(f"""
                SELECT c.oid, ({_REFERENCE_TABLE}) AS references_available
                FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
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
            """, relation["oid"], _CATALOG_COLUMN_LIMIT + 1)
        return SourceColumns(schema=schema, table=table, hasMore=len(columns) > _CATALOG_COLUMN_LIMIT,
                             referencesAvailable=True if relation["references_available"] else None,
                             columns=[SourceColumn(name=row["name"], type=row["type"], nullable=row["nullable"],
                                                   primaryKey=row["primary_key"], position=row["position"])
                                      for row in columns[:_CATALOG_COLUMN_LIMIT]])

    async def catalog_references(
        self, connection_id: str, *, schema: str, table: str, direction: SourceReferenceDirection,
        expected_updated_at: datetime,
    ) -> SourceReferences:
        # SQL identifiers below come only from the validated direction, never from caller input.
        root_rel, peer_rel = ("conrelid", "confrelid") if direction == "outgoing" else ("confrelid", "conrelid")
        async with self._postgres_catalog(connection_id, expected_updated_at=expected_updated_at) as connection:
            relation = await connection.fetchval(f"""
                SELECT c.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE {_CATALOG_RELATION_SCOPE} AND {_REFERENCE_TABLE}
                  AND n.nspname::text = $1 AND c.relname::text = $2
            """, schema, table)
            if relation is None:
                raise HTTPException(404, "Source relation is unavailable or not readable")
            rows = await connection.fetch(f"""
                SELECT DISTINCT n.nspname::text COLLATE "C" AS peer_schema,
                       c.relname::text COLLATE "C" AS peer_table,
                       proof.from_columns COLLATE "C" AS from_columns,
                       proof.to_columns COLLATE "C" AS to_columns
                FROM pg_catalog.pg_constraint f
                JOIN pg_catalog.pg_class c ON c.oid = f.{peer_rel}
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                CROSS JOIN LATERAL (
                    SELECT array_agg(src.attname::text ORDER BY pair.ord) AS from_columns,
                           array_agg(dst.attname::text ORDER BY pair.ord) AS to_columns
                    FROM ROWS FROM (pg_catalog.unnest(f.conkey), pg_catalog.unnest(f.confkey)) WITH ORDINALITY AS pair(from_att, to_att, ord)
                    JOIN pg_catalog.pg_attribute src ON src.attrelid = f.conrelid AND src.attnum = pair.from_att
                    JOIN pg_catalog.pg_attribute dst ON dst.attrelid = f.confrelid AND dst.attnum = pair.to_att
                    WHERE src.attnum > 0 AND NOT src.attisdropped
                      AND dst.attnum > 0 AND NOT dst.attisdropped
                      AND pg_catalog.has_column_privilege(src.attrelid, src.attnum, 'SELECT')
                      AND pg_catalog.has_column_privilege(dst.attrelid, dst.attnum, 'SELECT')
                    HAVING pg_catalog.count(*) = pg_catalog.cardinality(f.conkey)
                ) proof
                WHERE f.{root_rel} = $1::oid AND f.contype = 'f' AND f.convalidated AND f.conparentid = 0
                  AND pg_catalog.cardinality(f.conkey) BETWEEN 1 AND 32
                  AND pg_catalog.cardinality(f.conkey) = pg_catalog.cardinality(f.confkey)
                  AND {_CATALOG_RELATION_SCOPE} AND {_REFERENCE_TABLE}
                ORDER BY peer_schema, peer_table, from_columns, to_columns
                LIMIT $2
            """, relation, _CATALOG_REFERENCE_LIMIT + 1)
        return SourceReferences(
            direction=direction, hasMore=len(rows) > _CATALOG_REFERENCE_LIMIT,
            references=[
                SourceReference(schema=row["peer_schema"], table=row["peer_table"], pairs=[
                    SourceReferencePair(fromColumn=source, toColumn=target)
                    for source, target in zip(row["from_columns"], row["to_columns"], strict=True)
                ])
                for row in rows[:_CATALOG_REFERENCE_LIMIT]
            ],
        )

    async def save(self, payload, user_id, connection_id=None, *, expected_updated_at: datetime | None = None):
        if connection_id is None:
            params = await self._prepare_save(payload, user_id, uuid.uuid4(), None)
            async with self._session() as session:
                return await self._save_verified(session, params)
        async with self._session() as session:
            stored = await self._stored(connection_id, session=session, lock=" FOR UPDATE")
            await require_unconfigured(connection_id, stored["engine"], registry=self, row=stored, session=session)
            self._require_draft_revision(payload, stored, expected_updated_at)
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
            stored = (await session.execute(text("SELECT engine FROM public.ontology_data_connections WHERE id=:id FOR UPDATE"),
                                            {"id": uuid.UUID(connection_id)})).mappings().first()
            if stored is None:
                raise HTTPException(404, "Data source not found")
            await require_unconfigured(connection_id, stored["engine"], registry=self, session=session)
            row = (await session.execute(text("DELETE FROM public.ontology_data_connections WHERE id=:id RETURNING id"), {"id": uuid.UUID(connection_id)})).first()
            if row is None:
                raise HTTPException(404, "Data source not found")
        return {"deleted": True}


@lru_cache
def get_data_connections() -> DataConnections:
    return DataConnections()
