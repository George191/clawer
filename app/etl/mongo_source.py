"""Verify canonical Mongo RDS change messages against their saved source."""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import InvalidToken
from pymongo.read_concern import ReadConcern

from app.config.settings import settings
from app.etl.base import extract_meta
from app.etl.normalizers.base import safe_datetime
from app.etl.schema.catalogs import validate_table_name
from app.etl.source_fences import business_version
from app.records.collection_routes import collection_route_store
from app.records.field_names import normalize_record_fields
from app.records.values import require_canonical_record
from app.storage.connection_credentials import connection_cipher


def mongo_ods_input(raw: dict[str, Any]) -> dict[str, Any]:
    require_canonical_record(raw)
    identity = extract_meta(raw)
    kind = validate_table_name(identity["data_type"])
    if kind == "orbital":
        raise ValueError("Orbital ETL requires the PostgreSQL RDS event journal")
    if not all(isinstance(value, str) and value for value in identity.values()):
        raise ValueError("MongoDB RDS requires the canonical record identity")
    meta = raw["_meta"]
    if not isinstance(meta.get("write_token"), str) or not meta["write_token"] or not safe_datetime(meta.get("updated_at")):
        raise ValueError("MongoDB RDS requires a durable write token and source timestamp")
    return {**identity, "raw_data": {key: val for key, val in raw.items()
                                    if key not in {"_id", "_kafka_meta"}}}


def _business_image(document: dict[str, Any]) -> str:
    canonical = normalize_record_fields({key: value for key, value in document.items()
                                         if key not in {"_id", "_meta", "_kafka_meta"}})
    return json.dumps(canonical, sort_keys=True, ensure_ascii=False, default=str)


async def resolve_mongo_collection(metadata, database: str, source: str, data_type: str) -> str:
    route = await collection_route_store.resolve_for_write(source, data_type)
    collection = route["collection_name"]
    registered = await metadata.fetch_one(
        """SELECT id FROM app.helio_objects WHERE layer = 'rds' AND table_role = 'current'
            AND schema_name = :database AND table_name = :collection
            AND data_type = :data_type AND ddl_sql = '' AND is_active""",
        {"database": database, "collection": collection, "data_type": data_type},
    )
    if route.get("enabled") is False or registered is None:
        raise ValueError(f"No active MongoDB RDS binding for {source}/{data_type}")
    return collection


class MongoOdsSource:
    def __init__(self, metadata, connection: dict[str, Any]):
        from motor.motor_asyncio import AsyncIOMotorClient

        config = connection["config"]
        config = json.loads(config) if isinstance(config, str) else config
        secret = settings.connection_secret_key
        if not secret or secret.lower() == "changethis":
            raise ValueError("Configure a stable SECRET_KEY for saved MongoDB credentials")
        try:
            password = json.loads(connection_cipher(secret).decrypt(
                connection["credentials_ciphertext"].encode("ascii"),
            ))["password"]
            if not isinstance(password, str) or not password:
                raise ValueError("Missing MongoDB password")
        except (InvalidToken, KeyError, TypeError, UnicodeError, ValueError):
            raise ValueError("Saved MongoDB credentials cannot be decrypted") from None
        self.metadata = metadata
        self.database = config["database"]
        host = config["host"]
        self.client = AsyncIOMotorClient(
            host=f"[{host}]" if ":" in host else host, port=config["port"],
            username=config["username"], password=password, authSource=config.get("authDatabase") or "admin",
            tls=config["tls"], tz_aware=True, serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000, socketTimeoutMS=10000,
        )

    async def connect(self) -> None:
        await self.client[self.database].command({"ping": 1})

    async def data_types(self) -> list[str]:
        rows = await self.metadata.fetch_all(
            """SELECT DISTINCT data_type FROM app.helio_objects
                WHERE layer = 'rds' AND table_role = 'current' AND is_active
                  AND schema_name = :database AND ddl_sql = '' AND data_type <> ''
                ORDER BY data_type""",
            {"database": self.database},
        )
        return [validate_table_name(row["data_type"]) for row in rows]

    def close(self) -> None:
        self.client.close()

    async def is_current(self, source: dict[str, Any]) -> bool:
        source_meta = source["meta"]
        collection_name = await resolve_mongo_collection(
            self.metadata, self.database, source["data_source"], source["data_type"],
        )
        collection = self.client[self.database].get_collection(
            collection_name, read_concern=ReadConcern("majority"),
        )
        records = await collection.find({
            "_meta.record_id": source["record_id"], "_meta.template": source["data_source"],
            "_meta.data_type": source["data_type"],
        }).limit(2).to_list(length=2)
        if len(records) != 1:
            raise ValueError("MongoDB RDS requires exactly one persisted source record")
        current_meta = records[0]["_meta"]
        incoming_version, current_version = business_version(source_meta), business_version(current_meta)
        if incoming_version < current_version:
            return False
        if incoming_version > current_version:
            raise ValueError("MongoDB RDS message is ahead of the majority-committed source")
        if not source_meta.get("write_token") or source_meta["write_token"] != current_meta.get("write_token"):
            raise ValueError("MongoDB RDS cannot order different write tokens at the same source version")
        if _business_image(source["raw_data"]) != _business_image(records[0]):
            raise ValueError("MongoDB RDS message differs from its persisted business payload")
        return True
