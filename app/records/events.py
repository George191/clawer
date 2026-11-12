"""Durable orbital observations, independent of the mutable current projection."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from bson.codec_options import CodecOptions
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.records.download_resources import prepare_download_state
from app.records.values import record_values_equal, require_canonical_record
from app.utils.runtime_control import workspace_search_params

EVENT_COLLECTION = "record_events"
PENDING_EVENT_INDEX = "pending_orbital_fact_sequence"


async def ensure_event_indexes(storage, journal) -> None:
    key = (journal.name, PENDING_EVENT_INDEX, False)
    if key not in storage._initialized_indexes:
        await journal.create_index(
            [("record._meta.record_id", 1), ("record._meta.event_sequence", 1)],
            name=PENDING_EVENT_INDEX,
            partialFilterExpression={"delivery.status": "pending", "record._meta.data_type": "orbital"},
        )
        storage._initialized_indexes.add(key)


async def save_orbital_records(storage, collection, adapter, route, records, download_template):
    """Commit each complete snapshot and its event together, in observation order.

    Mongo transactions are mandatory. Session/transaction errors deliberately
    propagate: a current-only write would irretrievably lose an observation.
    """
    journal = storage._db[EVENT_COLLECTION]
    await ensure_event_indexes(storage, journal)
    ids = []
    stats = {"inserted": 0, "updated": 0, "unchanged": 0, "deleted": 0}
    run_stats = {}
    async with await storage._client.start_session() as session:
        for incoming in records:
            incoming = deepcopy(incoming)
            params = workspace_search_params(incoming.pop("_meta_search_params", None) or {})
            snapshot = (incoming.get("_meta") or {}).get("source_snapshot")
            cleaned = adapter.clean(incoming)
            record_id = storage._resolve_record_id(adapter.identity(cleaned))
            ids.append(record_id)

            async def commit(active_session):
                existing = await collection.find_one({"_meta.record_id": record_id}, session=active_session)
                now = datetime.now(timezone.utc)
                now = now.replace(microsecond=now.microsecond // 1000 * 1000)
                record = storage._build_record_with_meta(
                    template_name=route["collection_name"], data_type="orbital", record_id=record_id,
                    record=deepcopy(cleaned), search_params=deepcopy(params), now=now,
                )
                record["_meta"].update(adapter.metadata)
                if snapshot is not None:
                    record["_meta"]["source_snapshot"] = deepcopy(snapshot)
                record.pop("_id", None)
                record = await adapter.finalize(record, params)
                if existing and record_values_equal(storage._business_content(existing), storage._business_content(record)):
                    return "unchanged", None
                old_meta = (existing or {}).get("_meta") or {}
                meta = record["_meta"]
                meta["created_at"] = old_meta.get("created_at", now)
                # Keep download ownership until resource reconciliation invalidates it.
                for key, value in old_meta.items():
                    if key.startswith("download"):
                        meta[key] = deepcopy(value)
                prepare_download_state(existing, record, download_template, route["template_name"], "orbital")
                event_id = uuid4().hex
                meta.update(event_id=event_id, event_sequence=int(old_meta.get("event_sequence") or 0) + 1,
                            event_observed_at=now, write_token=event_id, sync_status="pending")
                require_canonical_record(record)
                await collection.replace_one({"_meta.record_id": record_id}, record, upsert=True, session=active_session)
                await journal.insert_one({"_id": event_id, "record": deepcopy(record),
                                          "delivery": {"status": "pending"}}, session=active_session)
                return "updated" if existing else "inserted", record

            # Concurrent first observations can race on the fact's unique index.
            for attempt in range(3):
                try:
                    kind, persisted = await session.with_transaction(
                        commit, read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"),
                    )
                    break
                except DuplicateKeyError:
                    if attempt == 2:
                        raise
            stats[kind] += 1
            if persisted:
                owner = persisted["_meta"].get("search_params") or {}
                identity = (str(owner.get("__workspace_task_id") or ""), str(owner.get("__workspace_run_id") or ""))
                if all(identity):
                    counts = run_stats.setdefault(identity, {"inserted": 0, "updated": 0})
                    counts[kind] += 1
    storage.last_save_stats = stats
    await adapter.after_save(run_stats)
    return ids


async def read_pending_events(storage, *, collections: list[str], limit: int, before=None) -> list[dict[str, Any]]:
    if not collections or limit <= 0:
        return []
    await storage._ensure_connection()
    journal = storage._db[EVENT_COLLECTION].with_options(codec_options=CodecOptions(tz_aware=True))
    await ensure_event_indexes(storage, journal)
    query = {"delivery.status": "pending", "record._meta.template": {"$in": collections},
             "record._meta.data_type": "orbital"}
    if before is not None:
        query["record._meta.event_observed_at"] = {"$lte": before}
    cursor = journal.find(query).sort([
        ("record._meta.record_id", 1), ("record._meta.event_sequence", 1),
    ]).limit(limit)
    return [doc["record"] async for doc in cursor]


async def read_event(storage, event_id: str) -> dict[str, Any]:
    await storage._ensure_connection()
    journal = storage._db[EVENT_COLLECTION].with_options(codec_options=CodecOptions(tz_aware=True))
    event = await journal.find_one({"_id": event_id})
    if event is None:
        raise ValueError(f"Orbital event {event_id} is missing from the durable journal")
    return event["record"]


async def acknowledge_event(storage, event_id: str) -> bool:
    """ACK delivery and mirror only this exact current version atomically.

    Return whether delivery was newly acknowledged, so mirror repairs on retry
    never increment run statistics a second time.
    """
    await storage._ensure_connection()
    journal = storage._db[EVENT_COLLECTION]

    async def commit(session):
        event = await journal.find_one({"_id": event_id}, session=session)
        if event is None:
            return False
        meta = event["record"]["_meta"]
        now = datetime.now(timezone.utc)
        now = now.replace(microsecond=now.microsecond // 1000 * 1000)
        synced_at = (event.get("delivery") or {}).get("synced_at") or now
        result = await journal.update_one(
            {"_id": event_id, "delivery.status": {"$ne": "synced"}},
            {"$set": {"delivery.status": "synced", "delivery.synced_at": synced_at}},
            session=session,
        )
        await storage._db[meta["template"]].update_one(
            {"_meta.record_id": meta["record_id"], "_meta.event_id": event_id,
             "_meta.write_token": event_id},
            {"$set": {"_meta.sync_status": "synced", "_meta.synced_at": synced_at}},
            session=session,
        )
        return bool(result.matched_count)

    async with await storage._client.start_session() as session:
        return await session.with_transaction(
            commit, read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"),
        )
