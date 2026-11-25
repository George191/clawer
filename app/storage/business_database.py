"""Resolve the explicitly selected business database from the system registry."""

from __future__ import annotations

import asyncio
import json
import ssl
from contextlib import asynccontextmanager

from cryptography.fernet import InvalidToken
from anyio import fail_after
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url

from app.storage.connection_credentials import connection_cipher
from app.storage.postgres_client import PostgresClient, _resolved_pg_url, get_pg_client
from app.config.settings import settings


class BusinessDatabaseNotConfigured(ValueError):
    pass


class LayerBindingChanged(RuntimeError):
    pass


@asynccontextmanager
async def layer_binding_lease(layers, *, expected=None, pg=None):
    """Hold the layers stable through a business write and its Kafka acknowledgement."""
    metadata = pg if pg is not None else get_pg_client()
    layers = sorted(set(layers))
    async with metadata.session() as session:
        # A separate READ COMMITTED statement sees the binder's commit after a lock wait.
        await session.execute(text("""SELECT pg_advisory_xact_lock_shared(hashtextextended('etl-layer:' || layer,0))
            FROM unnest(CAST(:layers AS text[])) AS selected(layer) ORDER BY layer"""), {"layers": layers})
        rows = (await session.execute(text("""SELECT b.layer, s.id::text AS id, s.updated_at
            FROM app.etl_layer_connections b JOIN app.ontology_data_connections s ON s.id=b.connection_id
            WHERE b.layer=ANY(CAST(:layers AS text[])) AND s.paused_at IS NULL"""),
            {"layers": layers})).mappings()
        current = {row["layer"]: (row["id"], row["updated_at"]) for row in rows}
        if expected is not None and current != expected:
            raise LayerBindingChanged("ETL layer bindings changed. Restart the worker to load the selected sources.")
        if expected is None:
            for layer in layers:
                client = _layer_clients.get(layer)
                if client and client.connection_id and (client.connection_id, client.revision) != current.get(layer):
                    await client.close()
        yield current


async def layer_connection_info(*, pg: PostgresClient | None = None) -> dict[str, dict]:
    pg = pg if pg is not None else get_pg_client()
    with fail_after(12):
        await pg.connect(max_retries=1)
        rows = await pg.fetch_all("""
            SELECT b.layer, s.id::text AS id, s.name, s.engine, s.updated_at, s.config->>'database' AS database
            FROM app.etl_layer_connections b
            JOIN app.ontology_data_connections s ON s.id=b.connection_id
            WHERE s.paused_at IS NULL ORDER BY b.layer
        """)
    return {row["layer"]: dict(row) for row in rows}


async def selected_layer_connections() -> dict[str, str]:
    return {layer: item["id"] for layer, item in (await layer_connection_info()).items()}


async def selected_layer_connection(layer: str, *, engine: str | None = "postgresql") -> dict:
    pg = get_pg_client()
    with fail_after(12):
        await pg.connect(max_retries=1)
        row = await pg.fetch_one("""
            SELECT s.id, s.name, s.engine, s.config, s.credentials_ciphertext, s.paused_at, s.updated_at
            FROM app.etl_layer_connections b
            JOIN app.ontology_data_connections s ON s.id=b.connection_id
            WHERE b.layer=:layer AND s.paused_at IS NULL
        """, {"layer": layer})
    if row is None:
        raise BusinessDatabaseNotConfigured(f"No active Data Connection is bound to layer {layer}")
    if engine is not None and row["engine"] != engine:
        raise BusinessDatabaseNotConfigured(f"The {layer} layer uses {row['engine']}; this operation requires {engine}")
    return row


async def selected_business_connection() -> dict:
    pg = get_pg_client()
    with fail_after(12):
        await pg.connect(max_retries=1)
        row = await pg.fetch_one("""
            SELECT source.id, source.name, source.engine, source.config,
                   source.credentials_ciphertext, source.paused_at
            FROM app.etl_business_connection binding
            JOIN app.ontology_data_connections source ON source.id = binding.connection_id
            WHERE binding.singleton
        """)
    if row is None:
        raise BusinessDatabaseNotConfigured("Select a PostgreSQL business database in Data Connections before running ETL")
    if row["engine"] != "postgresql" or row["paused_at"] is not None:
        raise BusinessDatabaseNotConfigured("The selected business database must be an active PostgreSQL connection")
    return row


def business_connection_options(row: dict) -> tuple[str, dict]:
    config = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
    if config["database"] == make_url(_resolved_pg_url()).database:
        raise BusinessDatabaseNotConfigured("The system database is reserved for app tables; select a separate business database")
    secret = settings.connection_secret_key
    if not secret or secret.lower() == "changethis":
        raise BusinessDatabaseNotConfigured("Configure a stable SECRET_KEY to read saved connection credentials")
    try:
        credentials = json.loads(connection_cipher(secret).decrypt(row["credentials_ciphertext"].encode("ascii")))
        password = credentials["password"]
    except (InvalidToken, KeyError, ValueError, UnicodeError, TypeError):
        raise BusinessDatabaseNotConfigured("Stored business database credentials cannot be decrypted; update this Data Connection") from None
    if not isinstance(password, str) or not password:
        raise BusinessDatabaseNotConfigured("The selected business database requires password credentials")
    url = URL.create("postgresql+asyncpg", username=config["username"], password=password,
                     host=config["host"], port=config["port"], database=config["database"])
    return url.render_as_string(hide_password=False), {"ssl": ssl.create_default_context() if config["tls"] else False}


class BusinessPostgresClient(PostgresClient):
    def __init__(self, layer: str | None = None) -> None:
        super().__init__(schema=None)
        self.layer = layer
        self.connection_id: str | None = None
        self.revision = None
        self._connection_lock = asyncio.Lock()

    @property
    def connection_url(self) -> str:
        if not self._url:
            raise BusinessDatabaseNotConfigured("Resolve the selected Data Connection before accessing its URL")
        return self._url

    @property
    def sslmode(self) -> str:
        return "verify-full" if self._connect_args.get("ssl") else "disable"

    async def _resolve(self) -> None:
        row = await selected_layer_connection(self.layer) if self.layer is not None else await selected_business_connection()
        self._url, self._connect_args = business_connection_options(row)
        self.connection_id = str(row["id"])
        self.revision = row.get("updated_at")

    async def resolve(self) -> None:
        async with self._connection_lock:
            if not self._connected:
                await self._resolve()

    async def connect(self, max_retries: int | None = None) -> None:
        async with self._connection_lock:
            if self._engine is not None and self._connected:
                return
            await self._resolve()
            await super().connect(max_retries=max_retries)

    async def close(self) -> None:
        async with self._connection_lock:
            await super().close()


_business_client: BusinessPostgresClient | None = None
_layer_clients: dict[str, BusinessPostgresClient] = {}


def get_layer_pg_client(layer: str) -> BusinessPostgresClient:
    if layer not in _layer_clients:
        _layer_clients[layer] = BusinessPostgresClient(layer=layer)
    return _layer_clients[layer]


def get_business_pg_client() -> BusinessPostgresClient:
    global _business_client
    if _business_client is None:
        _business_client = BusinessPostgresClient()
    return _business_client


def business_database_url() -> str:
    url = get_business_pg_client()._url
    if not url:
        raise BusinessDatabaseNotConfigured("Resolve the selected Data Connection before accessing the business database")
    return url
