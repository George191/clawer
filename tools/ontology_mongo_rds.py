"""Register an existing canonical RDS collection on the bound MongoDB connection."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.etl.mongo_source import MongoOdsSource
from app.etl.schema.catalogs import validate_table_name
from app.records.collection_routes import collection_route_store
from app.storage.business_database import selected_layer_connection
from app.storage.postgres_client import get_pg_client


async def register(source: str, data_type: str, *, apply: bool = False) -> dict:
    validate_table_name(data_type)
    if data_type == "orbital":
        raise ValueError("Orbital ETL requires the PostgreSQL RDS event journal")
    metadata = get_pg_client()
    mongo = None
    try:
        mongo = MongoOdsSource(metadata, await selected_layer_connection("rds", engine="mongodb"))
        route = await collection_route_store.resolve_for_write(source, data_type)
        collection = route["collection_name"]
        if route.get("enabled") is False:
            raise ValueError("The MongoDB collection route is disabled")
        database = mongo.client[mongo.database]
        names = await database.list_collection_names(filter={"name": collection})
        if collection not in names:
            raise ValueError("The routed collection does not exist in the bound MongoDB database")
        params = {"schema_name": mongo.database, "table_name": collection, "data_type": data_type}
        async with metadata.locked_transaction(f"ontology:rds:current:{mongo.database}:{collection}") as session:
            existing = (await session.execute(text("""
                SELECT id, data_type, is_active, ddl_sql FROM app.ontology_objects
                WHERE layer='rds' AND table_role='current' AND schema_name=:schema_name AND table_name=:table_name
                FOR UPDATE
            """), params)).mappings().first()
            if existing and (existing["ddl_sql"] or existing["data_type"] not in {"", data_type} or not existing["is_active"]):
                raise ValueError("The existing Ontology registration needs review before it can be used for MongoDB RDS")
            object_id = existing["id"] if existing else None
            if apply:
                result = await session.execute(text("""
                    INSERT INTO app.ontology_objects
                        (layer, table_role, schema_name, table_name, data_type, data_type_source,
                         provenance, resource_group)
                    VALUES ('rds', 'current', :schema_name, :table_name, :data_type, 'registration',
                            'registration', 'ingestion')
                    ON CONFLICT (layer, table_role, schema_name, table_name) DO UPDATE
                    SET data_type = EXCLUDED.data_type, data_type_source = EXCLUDED.data_type_source,
                        updated_at = now() RETURNING id
                """), params)
                object_id = result.scalar_one()
            return {"applied": apply, "id": object_id, **params}
    finally:
        if mongo is not None:
            mongo.close()
        await metadata.close()


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="An enabled collection route or template")
    parser.add_argument("data_type", help="The registered semantic ETL type")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(json.dumps(await register(args.source, args.data_type, apply=args.apply), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
