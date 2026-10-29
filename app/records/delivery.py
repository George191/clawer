"""Best-effort record delivery; persisted Mongo pending states remain the fallback."""

from __future__ import annotations

import asyncio
from datetime import timezone
from typing import Any

from bson.json_util import JSONOptions, dumps, loads

from app.config.settings import settings
from app.logger import get_logger
from app.storage.redis_client import RedisClient

logger = get_logger(__name__)

DELIVERY_TIMEOUT_SECONDS = 0.5
QUEUE_MAX_RECORDS = 1000
QUEUE_TTL_SECONDS = 3600
MAX_PAYLOAD_BYTES = 1024 * 1024
_JSON_OPTIONS = JSONOptions(tz_aware=True, tzinfo=timezone.utc)

_PUSH_SCRIPT = """
local length = redis.call('LLEN', KEYS[1])
local added = 0
for i = 3, #ARGV do
    if length + added >= tonumber(ARGV[1]) then break end
    redis.call('RPUSH', KEYS[1], ARGV[i])
    added = added + 1
end
if added > 0 then redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2])) end
return added
"""

_POP_SCRIPT = """
local records = {}
for _, key in ipairs(KEYS) do
    while #records < tonumber(ARGV[1]) do
        local value = redis.call('LPOP', key)
        if not value then break end
        records[#records + 1] = value
    end
    if #records >= tonumber(ARGV[1]) then break end
end
return records
"""


class RecordDelivery:
    """Exchange full record snapshots across the existing independent workers."""

    def __init__(self) -> None:
        self._connection = RedisClient(
            settings.task_redis_url, socket_connect_timeout=DELIVERY_TIMEOUT_SECONDS,
        )
        self._cursor: dict[str, int] = {}

    @staticmethod
    def _key(kind: str, collection: str) -> str:
        return f"spider:{settings.db_name}:records:{kind}:{collection}"

    async def publish(
        self, records: list[dict[str, Any]], *, sync: bool = True, download: bool = False,
    ) -> None:
        """Offer a batch within one deadline; overflow stays pending in Mongo."""
        if not records or not (sync or download):
            return
        try:
            await asyncio.wait_for(
                self._publish(records, sync=sync, download=download),
                timeout=DELIVERY_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            self._connection.mark_unavailable()
            logger.warning("Record delivery unavailable; Mongo will compensate: %s", exc)

    async def _publish(
        self, records: list[dict[str, Any]], *, sync: bool, download: bool,
    ) -> None:
        redis = await self._connection.ensure_connected()
        if redis is None:
            return
        batches: dict[str, list[str]] = {}
        for record in records:
            meta = record.get("_meta") or {}
            collection = meta.get("template")
            if not collection or not meta.get("record_id"):
                continue
            kinds = []
            if sync and meta.get("sync_status") != "synced":
                kinds.append("sync")
            if download and meta.get("download_status") == "pending":
                kinds.append("download")
            if not kinds:
                continue
            try:
                payload = dumps(
                    {key: value for key, value in record.items() if key != "_id"},
                    json_options=_JSON_OPTIONS, ensure_ascii=False, separators=(",", ":"),
                )
            except (TypeError, ValueError):
                logger.warning("Record snapshot cannot be encoded; Mongo will compensate")
                continue
            if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                continue
            for kind in kinds:
                batches.setdefault(self._key(kind, collection), []).append(payload)
        for key, payloads in batches.items():
            await redis.eval(
                _PUSH_SCRIPT, 1, key, QUEUE_MAX_RECORDS, QUEUE_TTL_SECONDS, *payloads,
            )

    async def receive(
        self, kind: str, collections: list[str], limit: int,
    ) -> list[dict[str, Any]]:
        """Pop FIFO snapshots, rotating the first collection to avoid starvation."""
        names = list(dict.fromkeys(collections))
        if not names or limit <= 0:
            return []
        start = self._cursor.get(kind, 0) % len(names)
        self._cursor[kind] = (start + 1) % len(names)
        names = names[start:] + names[:start]
        try:
            payloads = await asyncio.wait_for(
                self._receive(kind, names, limit), timeout=DELIVERY_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            self._connection.mark_unavailable()
            logger.warning("Record queue unavailable; Mongo will compensate: %s", exc)
            return []
        records = []
        for payload in payloads:
            try:
                record = loads(payload, json_options=_JSON_OPTIONS)
                meta = record.get("_meta") if isinstance(record, dict) else None
                if not isinstance(meta, dict) or not meta.get("record_id"):
                    raise ValueError("Missing record metadata")
                if meta.get("template") not in names:
                    raise ValueError("Unexpected collection")
            except Exception:
                logger.warning("Invalid record snapshot skipped; Mongo will compensate")
                continue
            records.append(record)
        return records

    async def _receive(self, kind: str, collections: list[str], limit: int) -> list[str]:
        redis = await self._connection.ensure_connected()
        if redis is None:
            return []
        keys = [self._key(kind, collection) for collection in collections]
        return await redis.eval(_POP_SCRIPT, len(keys), *keys, limit)

    async def close(self) -> None:
        """Release this worker's Redis connection."""
        await self._connection.close()
