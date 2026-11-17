"""Real, read-only external source probes and encrypted source persistence."""

from __future__ import annotations

import asyncio
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

from anyio import fail_after
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from fastapi import HTTPException
import httpx
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.storage.postgres_client import get_pg_client
from app.web.api.models.data_connections import DataConnectionPayload
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
            item.update(driverAvailable=True, prerequisite="")
        except HTTPException as failure:
            item.update(driverAvailable=False, prerequisite=failure.detail)
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


def _public(row) -> dict[str, Any]:
    data = dict(row)
    return {"id": str(data["id"]), "name": data["name"], "engine": data["engine"],
            "config": json.loads(data["config"]) if isinstance(data["config"], str) else data["config"],
            "credentialsConfigured": True, "createdBy": data["created_by"],
            "createdAt": data["created_at"].isoformat(), "updatedAt": data["updated_at"].isoformat(),
            "testedAt": data["tested_at"].isoformat()}


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
            await connection.close(timeout=_TIMEOUT)
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
        await asyncio.to_thread(synchronous_probe)


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
            with fail_after(12):
                await self._pg.connect(max_retries=1)
                async with self._pg.session() as session:
                    await session.execute(text("SET LOCAL statement_timeout = '10s'"))
                    yield session
        except HTTPException:
            raise
        except DBAPIError as error:
            code = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
            if code == "42P01":
                raise HTTPException(503, "External source registry is not initialized; run tools/ontology_data_connections.py --apply") from error
            raise HTTPException(503, "External source registry operation failed") from error
        except (ConnectionError, OSError, TimeoutError) as error:
            raise HTTPException(503, "External source registry is unavailable") from error

    async def list(self):
        async with self._session() as session:
            rows = (await session.execute(text(f"SELECT {_PUBLIC_COLUMNS} FROM public.ontology_data_connections ORDER BY lower(name), id"))).mappings()
            return [_public(row) for row in rows]

    async def _stored(self, connection_id: str):
        async with self._session() as session:
            row = (await session.execute(text(f"SELECT {_PUBLIC_COLUMNS}, credentials_ciphertext FROM public.ontology_data_connections WHERE id=:id"), {"id": uuid.UUID(connection_id)})).mappings().first()
            if row is None:
                raise HTTPException(404, "Data source not found")
            return dict(row)

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
        stored = await self._stored(connection_id) if connection_id else None
        credentials = await self._credentials(payload, stored)
        return await self._probe_result(payload, credentials)

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
        row = await self._stored(connection_id)
        payload = DataConnectionPayload(name=row["name"], engine=row["engine"], config=row["config"])
        credentials = await self._credentials(payload, row)
        result = await self._probe_result(payload, credentials)
        if result["ok"]:
            async with self._session() as session:
                updated = await session.execute(text("UPDATE public.ontology_data_connections SET tested_at=:tested WHERE id=:id AND updated_at=:version RETURNING id"),
                                                {"id": uuid.UUID(connection_id), "version": row["updated_at"], "tested": datetime.fromisoformat(result["testedAt"])})
                if updated.first() is None:
                    raise HTTPException(409, "The data source changed during testing; reload its current settings")
        return result

    async def save(self, payload, user_id, connection_id=None):
        cipher = credential_cipher()
        stored = await self._stored(connection_id) if connection_id else None
        credentials = await self._credentials(payload, stored)
        result = await self._probe_result(payload, credentials)
        if not result["ok"]:
            raise HTTPException(422, result["message"])
        identifier = uuid.UUID(connection_id) if connection_id else uuid.uuid4()
        params = {"id": identifier, "name": payload.name, "engine": payload.engine,
                  "config": json.dumps(payload.config.model_dump()),
                  "credentials": (stored["credentials_ciphertext"] if stored and not _secrets(payload)
                                  else cipher.encrypt(json.dumps(credentials).encode("utf-8")).decode("ascii")),
                  "user": str(user_id), "tested": datetime.fromisoformat(result["testedAt"])}
        if stored:
            params["version"] = stored["updated_at"]
        async with self._session() as session:
            if connection_id:
                sql = f"""UPDATE public.ontology_data_connections SET name=:name, engine=:engine,
                    config=CAST(:config AS jsonb), credentials_ciphertext=:credentials,
                    updated_at=clock_timestamp(), tested_at=:tested WHERE id=:id AND updated_at=:version RETURNING {_PUBLIC_COLUMNS}"""
            else:
                sql = f"""INSERT INTO public.ontology_data_connections
                    (id,name,engine,config,credentials_ciphertext,created_by,tested_at)
                    VALUES (:id,:name,:engine,CAST(:config AS jsonb),:credentials,:user,:tested)
                    RETURNING {_PUBLIC_COLUMNS}"""
            row = (await session.execute(text(sql), params)).mappings().first()
            if row is None:
                raise HTTPException(409, "The data source changed during verification; reload before saving")
            return _public(row)

    async def delete(self, connection_id):
        async with self._session() as session:
            row = (await session.execute(text("DELETE FROM public.ontology_data_connections WHERE id=:id RETURNING id"), {"id": uuid.UUID(connection_id)})).first()
            if row is None:
                raise HTTPException(404, "Data source not found")
        return {"deleted": True}


@lru_cache
def get_data_connections() -> DataConnections:
    return DataConnections()
