"""Bind DMDRS output to a saved source without claiming to control DMDRS."""

import asyncio
import json

from aiokafka import AIOKafkaConsumer
from fastapi import HTTPException
from sqlalchemy import text

from app.base.kafka import build_consumer_kwargs
from app.etl.schema.dameng_cdc import DAMENG_CDC_INSTALLED


async def require_no_dameng_binding(session, identifier):
    if await session.scalar(text(DAMENG_CDC_INSTALLED)):
        if await session.scalar(text("SELECT EXISTS (SELECT 1 FROM public.ontology_dameng_cdc_bindings WHERE connection_id=:id)"), {"id": identifier}):
            raise HTTPException(409, "Remove the DMDRS binding before editing or deleting this connection")


async def topic_status(topic):
    consumer = AIOKafkaConsumer(**{**build_consumer_kwargs(), "group_id": None, "enable_auto_commit": False,
                                  "request_timeout_ms": 5000})
    try:
        async with asyncio.timeout(8):
            await consumer.start()
            return topic in await consumer.topics()
    except Exception:
        raise HTTPException(503, "Cannot verify DMDRS output topic on the project's Kafka cluster") from None
    finally:
        await consumer.stop()


class SourceDamengCdc:
    def __init__(self, registry):
        self.registry = registry

    async def _source(self, identifier, session, expected=None):
        row = await self.registry._stored(str(identifier), session=session, lock=" FOR UPDATE")
        if row["engine"] != "dameng":
            raise HTTPException(422, "DMDRS bindings require a Dameng source")
        if expected is not None and row["updated_at"] != expected:
            raise HTTPException(409, "The source changed; reload before configuring DMDRS")
        return row

    async def describe(self, identifier):
        async with self.registry._session() as session:
            row = await self._source(identifier, session)
            ready = bool(await session.scalar(text(DAMENG_CDC_INSTALLED)))
            binding = None
            if ready:
                binding = (await session.execute(text("SELECT topic,tables,source_updated_at FROM public.ontology_dameng_cdc_bindings WHERE connection_id=:id"), {"id": identifier})).mappings().first()
            # Topic existence proves neither DMDRS health nor table coverage.
            return {"kind": "dmdrs", "schemaReady": ready, "state": "bound" if binding else "unconfigured",
                    "binding": {"topic": binding["topic"], "tables": binding["tables"],
                                "sourceChanged": binding["source_updated_at"] != row["updated_at"]} if binding else None,
                    "captureHealth": "externally_managed", "deliveryVerified": False}

    async def bind(self, identifier, payload):
        async with self.registry._session() as session:
            row = await self._source(identifier, session, payload.expectedUpdatedAt)
            if not await session.scalar(text(DAMENG_CDC_INSTALLED)):
                raise HTTPException(503, "Install DMDRS binding storage with tools/ontology_dameng_cdc.py --apply")
            await require_no_dameng_binding(session, identifier)
            if not await topic_status(payload.topic):
                raise HTTPException(422, "DMDRS output topic does not exist on the project's Kafka cluster")
            await session.execute(text("""INSERT INTO public.ontology_dameng_cdc_bindings
                (connection_id,source_updated_at,topic,tables) VALUES (:id,:revision,:topic,CAST(:tables AS jsonb))"""),
                                  {"id": identifier, "revision": row["updated_at"], "topic": payload.topic, "tables": json.dumps(payload.tables)})
        return {"bound": True, "deliveryVerified": False}

    async def remove(self, identifier, payload):
        async with self.registry._session() as session:
            await self._source(identifier, session, payload.expectedUpdatedAt)
            if not await session.scalar(text(DAMENG_CDC_INSTALLED)):
                raise HTTPException(503, "DMDRS binding storage is not installed")
            result = await session.execute(text("DELETE FROM public.ontology_dameng_cdc_bindings WHERE connection_id=:id RETURNING connection_id"), {"id": identifier})
            if result.first() is None:
                raise HTTPException(404, "No DMDRS binding is configured")
        return {"bound": False, "upstreamStopped": False}
