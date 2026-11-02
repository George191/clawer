"""Check Mongo Change Streams and register the Debezium MongoDB connector."""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from urllib.parse import quote

import httpx

from app.config.settings import settings


class SetupError(RuntimeError):
    """A safe setup error that contains no connection strings or server traces."""


def connector_config() -> dict[str, str]:
    if not all((settings.db_url, settings.db_name, settings.kafka_topic)):
        raise SetupError("SPIDER_DB_URL, SPIDER_DB_NAME and SPIDER_KAFKA_TOPIC are required")
    return {
        "connector.class": "io.debezium.connector.mongodb.MongoDbConnector",
        "tasks.max": "1",
        "mongodb.connection.string": settings.db_url,
        "topic.prefix": f"{settings.kafka_topic}-mongo-cdc",
        "database.include.list": re.escape(settings.db_name),
        "capture.scope": "database",
        "capture.target": settings.db_name,
        "capture.mode": "change_streams",
        # Existing pending records are drained by the workers' Mongo recovery scans.
        "snapshot.mode": "no_data",
        "tombstones.on.delete": "false",
        "key.converter": "org.apache.kafka.connect.json.JsonConverter",
        "value.converter": "org.apache.kafka.connect.json.JsonConverter",
        "key.converter.schemas.enable": "false",
        "value.converter.schemas.enable": "false",
        "topic.creation.default.replication.factor": "-1",
        "topic.creation.default.partitions": "1",
        "topic.creation.default.max.message.bytes": "16777216",
    }


async def check_mongo() -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(
        settings.db_url, serverSelectionTimeoutMS=10000,
        connectTimeoutMS=10000, socketTimeoutMS=10000,
    )
    try:
        hello = await client.admin.command("hello")
        if not hello.get("setName") and hello.get("msg") != "isdbgrid":
            raise SetupError("MongoDB must be a replica set or sharded cluster for Change Streams")
        # Opening and polling the cursor validates database-level changeStream/read access.
        async with client[settings.db_name].watch(max_await_time_ms=1000) as stream:
            await stream.try_next()
    finally:
        client.close()


def register_connector(connect_url: str, config: dict[str, str], *, name: str | None = None) -> None:
    name = name or f"{config['topic.prefix']}-{settings.db_name}"
    path = f"{connect_url.rstrip('/')}/connectors/{quote(name, safe='')}"
    with httpx.Client(timeout=30, trust_env=False) as client:
        response = client.put(f"{path}/config", json=config)
        if response.is_error:
            raise SetupError(f"Kafka Connect rejected connector configuration (HTTP {response.status_code})")
        deadline = time.monotonic() + 60
        while True:
            response = client.get(f"{path}/status")
            if response.is_error and response.status_code != 404:
                raise SetupError(f"Could not read connector status (HTTP {response.status_code})")
            if response.status_code != 404:
                status = response.json()
                states = [status.get("connector", {}).get("state")]
                states.extend(task.get("state") for task in status.get("tasks", []))
                if "FAILED" in states:
                    raise SetupError("Mongo CDC connector failed; inspect protected Kafka Connect logs")
                if len(states) > 1 and all(state == "RUNNING" for state in states):
                    return
            if time.monotonic() >= deadline:
                raise SetupError("Mongo CDC connector did not become RUNNING within 60 seconds")
            time.sleep(2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect-url", default="http://127.0.0.1:8083")
    parser.add_argument("--check", action="store_true", help="Check Mongo permissions/topology without registering")
    args = parser.parse_args()
    try:
        config = connector_config()
        asyncio.run(check_mongo())
        if args.check:
            print("Mongo Change Streams topology and database permissions verified")
        else:
            register_connector(args.connect_url, config)
            print("Mongo CDC connector and task are RUNNING")
        return 0
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
    except Exception as exc:
        # Driver/HTTP exceptions can include credentials, URLs or a reflected config.
        print(f"Mongo CDC setup failed ({type(exc).__name__}); check connectivity and permissions", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
