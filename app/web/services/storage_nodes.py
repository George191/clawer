"""Read real storage resources through their saved connection, never the warehouse URL."""

from contextlib import asynccontextmanager, closing
from base64 import b64encode
from datetime import date, datetime, time
from decimal import Decimal
import json
import math
import ssl
from urllib.parse import quote
from uuid import UUID

from anyio import fail_after, to_thread
from fastapi import HTTPException
import httpx
from pydantic import ValidationError

from app.web.api.models.data_connections import DataConnectionPayload, SourceColumn
from app.web.api.models.storage_nodes import (
    StorageNode, StorageNodeCatalog, StorageNodeColumns, StorageNodeKind, StorageNodePreview,
    StorageNodeRelation, StorageNodeRelations,
)
from app.web.services.data_connections import (
    _CATALOG_COLUMN_LIMIT, _CATALOG_KINDS, _CATALOG_RELATION_SCOPE, _CATALOG_SCHEMA_SCOPE,
    _CATALOG_SCHEMA_LIMIT, _CATALOG_TIMEOUT, _TIMEOUT, _driver, _failure_message, _http_endpoint,
)


_KINDS = {
    "postgresql": "schema", "mysql": "database", "elasticsearch": "index",
    "mongodb": "database", "clickhouse": "database", "oracle": "schema", "dameng": "schema",
    "milvus": "collection", "s3": "bucket", "redis": "database",
}
_MYSQL_SYSTEM_DATABASES = {"information_schema", "mysql", "performance_schema", "sys"}
_MAX_ROWS = 3000
_MAX_HTTP_BYTES = 4 * 1024 * 1024


def node_id(connection_id, kind: str, name: str) -> str:
    return f"{UUID(str(connection_id))}:{kind}:{quote(name, safe='')}"


def _name(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 255 or any(ord(char) < 32 for char in value):
        raise HTTPException(422, "A valid storage resource name is required")
    return value


def _quoted(value: str, delimiter: str = '"') -> str:
    return delimiter + _name(value).replace(delimiter, delimiter * 2) + delimiter


def _index_path(value: str) -> str:
    # Require one concrete, non-system index, never an index expression or another REST route.
    if value.startswith((".", "_", "-", "+")) or any(char in value for char in '\\/*?"<>| ,#:%[]'):
        raise HTTPException(422, "Select one concrete, non-system Elasticsearch index")
    return quote(_name(value), safe="")


def _limits(limit: int, offset: int = 0) -> None:
    if type(limit) is not int or type(offset) is not int or not 1 <= limit <= _MAX_ROWS or not 0 <= offset <= 5000:
        raise HTTPException(422, "Storage reads require a limit of 1–3000 and an offset of 0–5000")


def _columns(rows) -> StorageNodeColumns:
    return StorageNodeColumns(columns=[SourceColumn(**dict(row)) for row in rows[:_CATALOG_COLUMN_LIMIT]],
                              hasMore=len(rows) > _CATALOG_COLUMN_LIMIT)


def _preview_value(value):
    """Encode display values explicitly; transfer reads keep the driver's native types."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"$numberDouble": str(value)}
    if isinstance(value, Decimal):
        return {"$numberDecimal": str(value)}
    if isinstance(value, (datetime, date, time, UUID)):
        return value.isoformat() if not isinstance(value, UUID) else str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"$binary": {"base64": b64encode(bytes(value)).decode("ascii"),
                            "subType": f"{getattr(value, 'subtype', 0):02x}"}}
    if isinstance(value, dict):
        return {str(key): _preview_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_preview_value(item) for item in value]
    if type(value).__module__.startswith("bson."):
        from bson import json_util

        return json.loads(json_util.dumps(value))
    raise HTTPException(422, "The source contains a value type that this preview cannot display")


class StorageNodes:
    def __init__(self, registry):
        self.registry = registry

    @asynccontextmanager
    async def _source(self, connection_id, expected_updated_at: datetime | None):
        async with self.registry._session() as session:
            row = await self.registry._stored(str(connection_id), session=session, lock=" FOR SHARE")
            if expected_updated_at is not None and row["updated_at"] != expected_updated_at:
                raise HTTPException(409, {"code": "source_revision_changed",
                                          "message": "The saved connection changed; reload its storage nodes"})
            try:
                config = json.loads(row["config"]) if isinstance(row["config"], str) else row["config"]
                payload = DataConnectionPayload(name=row["name"], engine=row["engine"], config=config)
            except (ValidationError, ValueError, TypeError):
                raise HTTPException(503, "Saved source settings are invalid; verify the connection") from None
            credentials = await self.registry._credentials(payload, row)
            try:
                with fail_after(_CATALOG_TIMEOUT):
                    yield row, payload, credentials
            except HTTPException:
                raise
            except TimeoutError:
                raise HTTPException(504, "The source read timed out") from None
            except Exception as error:
                raise HTTPException(502, _failure_message(error)) from None

    @staticmethod
    def _require_kind(engine: str, kind: StorageNodeKind, scope: str):
        _name(scope)
        if _KINDS[engine] != kind:
            raise HTTPException(422, f"This connector uses {_KINDS[engine]} storage nodes")

    async def nodes(self, connection_id, *, search: str = "", expected_updated_at: datetime | None = None) -> StorageNodeCatalog:
        if not isinstance(search, str) or len(search) > 100 or any(ord(char) < 32 for char in search):
            raise HTTPException(422, "Storage node search must be at most 100 characters without controls")
        async with self._source(connection_id, expected_updated_at) as (row, payload, credentials):
            kind = _KINDS[payload.engine]
            names = await self._node_names(row, payload, credentials, search)
            return StorageNodeCatalog(nodes=[StorageNode(
                id=node_id(connection_id, kind, name), connectionId=connection_id, kind=kind, name=name,
                engine=payload.engine, connectionName=payload.name, sourceUpdatedAt=row["updated_at"],
            ) for name in names[:_CATALOG_SCHEMA_LIMIT]], hasMore=len(names) > _CATALOG_SCHEMA_LIMIT)

    async def _node_names(self, row, payload, credentials, search):
        engine = payload.engine
        if engine == "postgresql":
            async with self.registry._postgres_catalog_row(row) as connection:
                return [record["name"] for record in await connection.fetch(f"""
                    SELECT n.nspname::text AS name FROM pg_catalog.pg_namespace n
                    WHERE {_CATALOG_SCHEMA_SCOPE}
                      AND ($1::text='' OR position(lower($1::text) in lower(n.nspname::text))>0)
                    ORDER BY n.nspname::text COLLATE "C" LIMIT $2
                """, search, _CATALOG_SCHEMA_LIMIT + 1)]
        if engine == "mysql":
            return await self._mysql(payload, credentials, "nodes", search=search)
        if engine == "elasticsearch":
            data = await self._http(payload, credentials, "/_resolve/index/*", params={"expand_wildcards": "open"})
            names = {item["name"] for item in data["indices"] if not item["name"].startswith(".")}
            return sorted(name for name in names if search.casefold() in name.casefold())[:_CATALOG_SCHEMA_LIMIT + 1]
        if engine == "clickhouse":
            data = await self._clickhouse(payload, credentials,
                "SELECT name FROM system.databases WHERE name NOT IN ('system','information_schema','INFORMATION_SCHEMA') "
                "AND positionCaseInsensitive(name,{search:String})>0 ORDER BY name LIMIT 101", {"param_search": search})
            return [item["name"] for item in data["data"]]
        if engine == "mongodb":
            return await self._mongo(payload, credentials, "nodes", search=search)
        if engine == "milvus":
            return await self._milvus(payload, credentials, "nodes", search=search)
        if engine == "oracle":
            return await self._oracle(payload, credentials, "nodes", search=search)
        if engine == "dameng":
            raise HTTPException(422, "Dameng storage discovery requires a supported metadata reader")
        # Redis and S3 credentials are scoped to the configured database/bucket.
        name = payload.config.bucket if engine == "s3" else payload.config.database
        return [name] if search.casefold() in name.casefold() else []

    async def relations(self, connection_id, kind: StorageNodeKind, scope: str, *, search: str = "",
                        limit: int = 50, offset: int = 0, expected_updated_at: datetime | None = None) -> StorageNodeRelations:
        _limits(limit, offset)
        async with self._source(connection_id, expected_updated_at) as (row, payload, credentials):
            self._require_kind(payload.engine, kind, scope)
            values = await self._relations(row, payload, credentials, scope, search, limit + 1, offset)
            return StorageNodeRelations(relations=[StorageNodeRelation(**item) for item in values[:limit]],
                                        hasMore=len(values) > limit)

    async def _relations(self, row, payload, credentials, scope, search, limit, offset):
        engine = payload.engine
        if engine == "postgresql":
            async with self.registry._postgres_catalog_row(row) as connection:
                await self._pg_scope(connection, scope)
                records = await connection.fetch(f"""
                    SELECT c.relname::text AS name, c.relkind::text AS kind
                    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                    WHERE {_CATALOG_RELATION_SCOPE} AND n.nspname::text=$1
                      AND ($2::text='' OR position(lower($2::text) in lower(c.relname::text))>0)
                    ORDER BY c.relname::text COLLATE "C" LIMIT $3 OFFSET $4
                """, scope, search, limit, offset)
                return [{"name": record["name"], "kind": _CATALOG_KINDS[record["kind"]]} for record in records]
        if engine == "mysql":
            return await self._mysql(payload, credentials, "relations", scope=scope, search=search, limit=limit, offset=offset)
        if engine == "elasticsearch":
            await self._es_mapping(payload, credentials, scope)
            return [{"name": scope, "kind": "index"}] if not offset and search.casefold() in scope.casefold() else []
        if engine == "clickhouse":
            data = await self._clickhouse(payload, credentials,
                "SELECT name, if(engine IN ('View','MaterializedView'),'view','table') AS kind FROM system.tables "
                "WHERE database={scope:String} AND positionCaseInsensitive(name,{search:String})>0 ORDER BY name "
                f"LIMIT {limit} OFFSET {offset}", {"param_scope": scope, "param_search": search})
            return data["data"]
        if engine == "mongodb":
            return await self._mongo(payload, credentials, "relations", scope=scope, search=search, limit=limit, offset=offset)
        if engine == "milvus":
            await self._milvus(payload, credentials, "exists", scope=scope)
            return [{"name": scope, "kind": "collection"}] if not offset and search.casefold() in scope.casefold() else []
        if engine == "oracle":
            return await self._oracle(payload, credentials, "relations", scope=scope, search=search, limit=limit, offset=offset)
        if engine == "s3":
            return await self._s3(payload, credentials, scope, search, limit, offset)
        raise HTTPException(422, f"Resource exploration is not supported by the {engine} connector")

    async def columns(self, connection_id, kind: StorageNodeKind, scope: str, relation: str, *,
                      expected_updated_at: datetime | None = None) -> StorageNodeColumns:
        _name(relation)
        async with self._source(connection_id, expected_updated_at) as (row, payload, credentials):
            self._require_kind(payload.engine, kind, scope)
            return await self._columns(row, payload, credentials, scope, relation)

    async def _columns(self, row, payload, credentials, scope, relation):
        engine = payload.engine
        if engine == "postgresql":
            async with self.registry._postgres_catalog_row(row) as connection:
                oid = await self._pg_relation(connection, scope, relation)
                records = await connection.fetch("""
                    SELECT a.attname::text AS name, format_type(a.atttypid,a.atttypmod) AS type,
                           NOT a.attnotnull AS nullable, a.attnum AS position,
                           EXISTS (SELECT 1 FROM pg_catalog.pg_index i WHERE i.indrelid=a.attrelid
                             AND i.indisprimary AND i.indisvalid AND i.indisready AND i.indislive
                             AND EXISTS (SELECT 1 FROM unnest(i.indkey) WITH ORDINALITY AS pk(attnum,ord)
                                         WHERE pk.attnum=a.attnum AND pk.ord<=i.indnkeyatts)) AS "primaryKey"
                    FROM pg_catalog.pg_attribute a WHERE a.attrelid=$1::oid AND a.attnum>0
                      AND NOT a.attisdropped AND has_column_privilege(a.attrelid,a.attnum,'SELECT')
                    ORDER BY a.attnum LIMIT $2
                """, oid, _CATALOG_COLUMN_LIMIT + 1)
                return _columns(records)
        if engine == "mysql":
            return _columns(await self._mysql(payload, credentials, "columns", scope=scope, relation=relation))
        if engine == "elasticsearch":
            self._same_index(scope, relation)
            mapping = await self._es_mapping(payload, credentials, scope)
            columns = [{"name": "_id", "type": "keyword", "nullable": False, "primaryKey": True, "position": 1}]
            for name, field in sorted(mapping.get("properties", {}).items()):
                if name == "_id":
                    raise HTTPException(422, "The index source uses a reserved metadata field")
                columns.append({"name": name, "type": field.get("type", "object"), "nullable": True,
                                "primaryKey": False, "position": len(columns) + 1})
            return _columns(columns)
        if engine == "clickhouse":
            data = await self._clickhouse(payload, credentials,
                "SELECT name,type,startsWith(type,'Nullable(') AS nullable,is_in_primary_key AS primaryKey,position "
                "FROM system.columns WHERE database={scope:String} AND table={relation:String} ORDER BY position LIMIT 201",
                {"param_scope": scope, "param_relation": relation})
            if not data["data"]:
                raise HTTPException(404, "Source table is unavailable")
            return _columns(data["data"])
        if engine == "milvus":
            if scope != relation:
                raise HTTPException(422, "The relation must match the selected collection")
            return _columns(await self._milvus(payload, credentials, "columns", scope=scope))
        if engine == "oracle":
            return _columns(await self._oracle(payload, credentials, "columns", scope=scope, relation=relation))
        raise HTTPException(422, f"Column metadata is not supported by the {engine} connector")

    async def preview(self, connection_id, kind: StorageNodeKind, scope: str, relation: str, *,
                      limit: int = 20, expected_updated_at: datetime | None = None) -> StorageNodePreview:
        _limits(limit)
        async with self._source(connection_id, expected_updated_at) as (row, payload, credentials):
            self._require_kind(payload.engine, kind, scope)
            records = await self._rows(row, payload, credentials, scope, _name(relation), limit + 1)
            visible = [_preview_value(record) for record in records[:limit]]
            columns = list(dict.fromkeys(key for record in visible for key in record))
            return StorageNodePreview(columns=columns, rows=visible, hasMore=len(records) > limit)

    async def read_rows(self, connection_id, kind: StorageNodeKind, scope: str, relation: str, *,
                        limit: int = 100, expected_updated_at: datetime) -> list[dict]:
        _limits(limit)
        if expected_updated_at is None:
            raise HTTPException(422, "A reviewed connection revision is required to read transfer rows")
        async with self._source(connection_id, expected_updated_at) as (row, payload, credentials):
            self._require_kind(payload.engine, kind, scope)
            records = await self._rows(row, payload, credentials, scope, _name(relation), limit + 1)
            if len(records) > limit:
                raise HTTPException(422, {"code": "source_row_limit_exceeded",
                                          "message": "The source exceeds this transfer's row limit; no partial source is returned"})
            return records

    async def _rows(self, row, payload, credentials, scope, relation, limit):
        engine = payload.engine
        if engine == "postgresql":
            async with self.registry._postgres_catalog_row(row) as connection:
                oid = await self._pg_relation(connection, scope, relation)
                columns = await connection.fetch("""
                    SELECT a.attname::text AS name FROM pg_catalog.pg_attribute a
                    WHERE a.attrelid=$1::oid AND a.attnum>0 AND NOT a.attisdropped
                      AND has_column_privilege(a.attrelid,a.attnum,'SELECT')
                    ORDER BY a.attnum LIMIT $2
                """, oid, _CATALOG_COLUMN_LIMIT + 1)
                if not columns:
                    raise HTTPException(404, "No source columns are readable")
                if len(columns) > _CATALOG_COLUMN_LIMIT:
                    raise HTTPException(422, "The source exceeds the readable column limit")
                projection = ",".join(_quoted(column["name"]) for column in columns)
                records = await connection.fetch(f"SELECT {projection} FROM {_quoted(scope)}.{_quoted(relation)} LIMIT $1", limit)
                return [dict(record) for record in records]
        if engine == "mysql":
            return await self._mysql(payload, credentials, "rows", scope=scope, relation=relation, limit=limit)
        if engine == "elasticsearch":
            self._same_index(scope, relation)
            await self._es_mapping(payload, credentials, scope)
            data = await self._http(payload, credentials, f"/{_index_path(scope)}/_search", method="POST",
                params={"allow_partial_search_results": "false"},
                body={"size": limit, "query": {"match_all": {}}, "timeout": "5s", "track_total_hits": False})
            if data.get("timed_out") or data.get("_shards", {}).get("failed", 0):
                raise HTTPException(502, "Elasticsearch did not return a complete source read")
            records = []
            for hit in data["hits"]["hits"]:
                if not isinstance(hit.get("_source"), dict) or "_id" in hit["_source"]:
                    raise HTTPException(422, "The index must expose source documents without reserved metadata fields")
                records.append({"_id": hit["_id"], **hit["_source"]})
            return records
        if engine == "clickhouse":
            data = await self._clickhouse(payload, credentials,
                f"SELECT * FROM {_quoted(scope, '`')}.{_quoted(relation, '`')} LIMIT {limit}")
            return data["data"]
        if engine == "mongodb":
            return await self._mongo(payload, credentials, "rows", scope=scope, relation=relation, limit=limit)
        if engine == "oracle":
            return await self._oracle(payload, credentials, "rows", scope=scope, relation=relation, limit=limit)
        raise HTTPException(422, f"Row reads are not supported by the {engine} connector")

    @staticmethod
    async def _pg_scope(connection, scope):
        allowed = await connection.fetchval(f"SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n WHERE {_CATALOG_SCHEMA_SCOPE} AND n.nspname::text=$1)", scope)
        if not allowed:
            raise HTTPException(404, "Source schema is unavailable or not readable")

    @staticmethod
    async def _pg_relation(connection, scope, relation):
        oid = await connection.fetchval(f"""
            SELECT c.oid FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE {_CATALOG_RELATION_SCOPE} AND n.nspname::text=$1 AND c.relname::text=$2
        """, scope, relation)
        if oid is None:
            raise HTTPException(404, "Source relation is unavailable or not readable")
        return oid

    @staticmethod
    def _same_index(scope, relation):
        if scope != relation:
            raise HTTPException(422, "The relation must match the selected Elasticsearch index")

    async def _es_mapping(self, payload, credentials, scope):
        data = await self._http(payload, credentials, f"/{_index_path(scope)}/_mapping")
        if not isinstance(data, dict) or set(data) != {scope}:
            raise HTTPException(422, "Select one concrete Elasticsearch index; aliases and data streams are not index nodes")
        return data[scope]["mappings"]

    async def _http(self, payload, credentials, path, *, method="GET", params=None, body=None):
        config = payload.config
        auth = httpx.BasicAuth(config.username, credentials["password"]) if config.auth == "password" else None
        headers = {"Authorization": f"ApiKey {credentials['apiKey']}"} if config.auth == "api_key" else {}
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False, follow_redirects=False) as client:
            async with client.stream(method, _http_endpoint(config) + path, auth=auth, headers=headers,
                                     params=params, json=body) as response:
                if response.status_code == 404:
                    raise HTTPException(404, "Source resource is unavailable")
                response.raise_for_status()
                result = bytearray()
                async for chunk in response.aiter_bytes():
                    result.extend(chunk)
                    if len(result) > _MAX_HTTP_BYTES:
                        raise HTTPException(422, "The source response exceeds the read size limit")
                return json.loads(result)

    async def _clickhouse(self, payload, credentials, sql, params=None):
        return await self._http(payload, credentials, "/", params={"query": sql + " FORMAT JSON", "readonly": "1",
            "max_execution_time": "5", "database": payload.config.database, **(params or {})})

    async def _mysql(self, payload, credentials, operation, *, scope=None, relation=None, search="", limit=0, offset=0):
        def run():
            config = payload.config
            driver = _driver("mysql")
            with closing(driver.connect(host=config.host, port=config.port, database=config.database,
                    user=config.username, password=credentials["password"], autocommit=False,
                    connect_timeout=_TIMEOUT, read_timeout=_TIMEOUT, write_timeout=_TIMEOUT,
                    ssl=ssl.create_default_context() if config.tls else None, ssl_disabled=not config.tls)) as connection:
                with connection.cursor() as cursor:
                    if config.tls:
                        cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                        cipher = cursor.fetchone()
                        if not cipher or not cipher[1]:
                            raise ssl.SSLError("TLS was not negotiated")
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute("START TRANSACTION READ ONLY")
                    try:
                        if operation == "nodes":
                            cursor.execute("SHOW DATABASES")
                            return sorted(str(record[0]) for record in cursor.fetchall()
                                          if record[0].casefold() not in _MYSQL_SYSTEM_DATABASES
                                          and search.casefold() in record[0].casefold())[:_CATALOG_SCHEMA_LIMIT + 1]
                        if scope.casefold() in _MYSQL_SYSTEM_DATABASES:
                            raise HTTPException(404, "System databases cannot be selected as source nodes")
                        cursor.execute("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s", (scope,))
                        if cursor.fetchone() is None:
                            raise HTTPException(404, "Source database is unavailable")
                        if operation == "relations":
                            cursor.execute("SELECT TABLE_NAME,TABLE_TYPE FROM information_schema.TABLES "
                                "WHERE TABLE_SCHEMA=%s AND LOCATE(LOWER(%s),LOWER(TABLE_NAME))>0 "
                                "ORDER BY TABLE_NAME LIMIT %s OFFSET %s", (scope, search, limit, offset))
                            return [{"name": record[0], "kind": "view" if record[1] == "VIEW" else "table"} for record in cursor.fetchall()]
                        cursor.execute("SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (scope, relation))
                        if cursor.fetchone() is None:
                            raise HTTPException(404, "Source table is unavailable")
                        if operation == "columns":
                            cursor.execute("SELECT COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COLUMN_KEY,ORDINAL_POSITION "
                                "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
                                "ORDER BY ORDINAL_POSITION LIMIT 201", (scope, relation))
                            return [{"name": record[0], "type": record[1], "nullable": record[2] == "YES",
                                     "primaryKey": record[3] == "PRI", "position": record[4]} for record in cursor.fetchall()]
                        cursor.execute(f"SELECT * FROM {_quoted(scope, '`')}.{_quoted(relation, '`')} LIMIT %s", (limit,))
                        names = [column[0] for column in cursor.description]
                        return [dict(zip(names, record)) for record in cursor.fetchall()]
                    finally:
                        connection.rollback()
        return await to_thread.run_sync(run)

    async def _mongo(self, payload, credentials, operation, *, scope=None, relation=None, search="", limit=0, offset=0):
        def run():
            config = payload.config
            driver = _driver("mongodb")
            host = f"[{config.host}]" if ":" in config.host else config.host
            with driver.MongoClient(host=host, port=config.port, username=config.username,
                    password=credentials["password"], authSource=config.authDatabase, tls=config.tls,
                    directConnection=True, timeoutMS=_TIMEOUT * 1000, retryReads=False, retryWrites=False) as client:
                if operation == "nodes":
                    return sorted(name for name in client.list_database_names()
                                  if name not in {"admin", "config", "local"}
                                  and search.casefold() in name.casefold())[:_CATALOG_SCHEMA_LIMIT + 1]
                if scope in {"admin", "config", "local"}:
                    raise HTTPException(404, "System databases cannot be selected as source nodes")
                database = client[scope]
                names = database.list_collection_names()
                if operation == "relations":
                    return [{"name": name, "kind": "collection"} for name in sorted(names)
                            if not name.startswith("system.") and search.casefold() in name.casefold()][offset:offset + limit]
                if relation not in names or relation.startswith("system."):
                    raise HTTPException(404, "Source collection is unavailable")
                with database[relation].find({}).limit(limit).max_time_ms(_TIMEOUT * 1000) as cursor:
                    return list(cursor)
        return await to_thread.run_sync(run)

    async def _milvus(self, payload, credentials, operation, *, scope=None, search=""):
        def run():
            config = payload.config
            options = {"token": credentials["token"]} if config.auth == "token" else {"user": config.username, "password": credentials["password"]}
            client = _driver("milvus").MilvusClient(uri=_http_endpoint(config), db_name=config.database,
                                                    timeout=_TIMEOUT, **options)
            try:
                names = client.list_collections(timeout=_TIMEOUT)
                if operation == "nodes":
                    return sorted(name for name in names if search.casefold() in name.casefold())[:_CATALOG_SCHEMA_LIMIT + 1]
                if scope not in names:
                    raise HTTPException(404, "Source collection is unavailable")
                if operation == "exists":
                    return True
                info = client.describe_collection(collection_name=scope, timeout=_TIMEOUT)
                return [{"name": field["name"], "type": str(field["type"]), "nullable": field.get("nullable", False),
                         "primaryKey": field.get("is_primary", False), "position": index + 1}
                        for index, field in enumerate(info["fields"])]
            finally:
                client.close()
        return await to_thread.run_sync(run)

    async def _oracle(self, payload, credentials, operation, *, scope=None, relation=None, search="", limit=0, offset=0):
        config = payload.config
        driver = _driver("oracle")
        params = driver.ConnectParams(host=config.host, port=config.port, service_name=config.database,
            protocol="tcps" if config.tls else "tcp", tcp_connect_timeout=_TIMEOUT, retry_count=0,
            ssl_server_dn_match=True, ssl_context=ssl.create_default_context() if config.tls else None)
        async with await driver.connect_async(user=config.username, password=credentials["password"], params=params) as connection:
            connection.call_timeout = _TIMEOUT * 1000
            with connection.cursor() as cursor:
                await cursor.execute("SET TRANSACTION READ ONLY")
                try:
                    if operation == "nodes":
                        await cursor.execute("SELECT DISTINCT owner FROM all_tables WHERE owner NOT IN ('SYS','SYSTEM') "
                            "AND INSTR(LOWER(owner),LOWER(:search))>0 ORDER BY owner FETCH FIRST 101 ROWS ONLY", search=search)
                        return [record[0] for record in await cursor.fetchall()]
                    if scope in {"SYS", "SYSTEM"}:
                        raise HTTPException(404, "System schemas cannot be selected as source nodes")
                    if operation == "relations":
                        await cursor.execute("SELECT table_name FROM all_tables WHERE owner=:scope "
                            "AND INSTR(LOWER(table_name),LOWER(:search))>0 ORDER BY table_name "
                            "OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY", scope=scope, search=search, offset=offset, limit=limit)
                        return [{"name": record[0], "kind": "table"} for record in await cursor.fetchall()]
                    await cursor.execute("SELECT table_name FROM all_tables WHERE owner=:scope AND table_name=:relation", scope=scope, relation=relation)
                    if await cursor.fetchone() is None:
                        raise HTTPException(404, "Source table is unavailable")
                    if operation == "columns":
                        await cursor.execute("SELECT c.column_name,c.data_type,c.nullable,c.column_id, "
                            "CASE WHEN EXISTS (SELECT 1 FROM all_constraints k JOIN all_cons_columns f "
                            "ON f.owner=k.owner AND f.constraint_name=k.constraint_name WHERE k.constraint_type='P' "
                            "AND k.owner=c.owner AND k.table_name=c.table_name AND f.column_name=c.column_name) THEN 1 ELSE 0 END "
                            "FROM all_tab_columns c WHERE c.owner=:scope AND c.table_name=:relation "
                            "ORDER BY c.column_id FETCH FIRST 201 ROWS ONLY", scope=scope, relation=relation)
                        return [{"name": record[0], "type": record[1], "nullable": record[2] == "Y", "position": record[3], "primaryKey": bool(record[4])}
                                for record in await cursor.fetchall()]
                    await cursor.execute(f"SELECT * FROM {_quoted(scope)}.{_quoted(relation)} FETCH FIRST :limit ROWS ONLY", limit=limit)
                    names = [item[0] for item in cursor.description]
                    return [dict(zip(names, record)) for record in await cursor.fetchall()]
                finally:
                    await connection.rollback()

    async def _s3(self, payload, credentials, scope, search, limit, offset):
        if scope != payload.config.bucket:
            raise HTTPException(404, "Select the bucket configured on this connection")
        if offset:
            raise HTTPException(422, "Object listing does not support numeric offsets; narrow the prefix")
        def run():
            from botocore.config import Config

            config = payload.config
            options = Config(signature_version="s3v4", s3={"addressing_style": config.addressingStyle or "path"},
                proxies={}, connect_timeout=_TIMEOUT, read_timeout=_TIMEOUT, retries={"total_max_attempts": 1})
            with closing(_driver("s3").Session().client("s3", endpoint_url=_http_endpoint(config),
                    region_name=config.region, config=options, aws_access_key_id=config.username,
                    aws_secret_access_key=credentials["secretAccessKey"])) as client:
                result = client.list_objects_v2(Bucket=scope, Prefix=search, MaxKeys=min(limit, 1000))
                if result.get("IsTruncated"):
                    raise HTTPException(422, "The object prefix has more results; narrow it before selecting an object")
                return [{"name": item["Key"], "kind": "object"} for item in result.get("Contents", [])]
        return await to_thread.run_sync(run)
