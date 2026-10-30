"""Persist successful source and record fingerprints for snapshot-only sites."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.crawler.checkpoint import PageCheckpointStore
from app.utils.path import get_nested_value
from app.utils.record_id import resolve_record_id


def _fingerprint(content: Any) -> str:
    if isinstance(content, bytes):
        raw = content
    elif isinstance(content, str):
        raw = content.encode("utf-8")
    else:
        raw = json.dumps(
            content, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def record_fingerprint(record: dict[str, Any], dedup_fields: list[str]) -> tuple[str, str]:
    """Return the same stable record identity and business hash used by crawls."""
    business = {
        key: value for key, value in record.items()
        if key not in {"_meta", "_meta_search_params", "data_type"}
    }
    identity = {field: get_nested_value(record, field) for field in dedup_fields}
    if not identity or all(value is None or value == "" for value in identity.values()):
        identity = business
    return f"record:{resolve_record_id(identity)}", _fingerprint(business)


class SnapshotStore:
    """Stage hashes in memory; publish them only after the whole crawl succeeds."""

    def __init__(self, checkpoint_store: PageCheckpointStore) -> None:
        self._checkpoint_store = checkpoint_store
        self.key = f"incremental_snapshot:{checkpoint_store.task_id or checkpoint_store.key}"
        self._pending: dict[str, str] = {}

    async def changed(self, identity: str, content: Any) -> bool:
        field = f"source:{_fingerprint(identity)}"
        digest = _fingerprint(content)
        previous = await self._checkpoint_store.run_redis_operation(
            lambda redis: redis.hget(self.key, field)
        )
        if previous == digest:
            return False
        self._pending[field] = digest
        return True

    async def get_metadata(self, identity: str) -> dict[str, Any] | None:
        field = f"meta:{_fingerprint(identity)}"
        raw = await self._checkpoint_store.run_redis_operation(
            lambda redis: redis.hget(self.key, field)
        )
        return json.loads(raw) if raw else None

    async def set_metadata(self, identity: str, value: dict[str, Any]) -> None:
        metadata = {
            key: value[key] for key in ("etag", "last_modified", "hash") if key in value
        }
        self._pending[f"meta:{_fingerprint(identity)}"] = json.dumps(
            metadata, sort_keys=True, ensure_ascii=False,
        )

    async def filter_records(
        self, records: list[dict[str, Any]], dedup_fields: list[str],
    ) -> list[dict[str, Any]]:
        if not records:
            return []
        fingerprints = [record_fingerprint(record, dedup_fields) for record in records]
        fields = [field for field, _ in fingerprints]
        previous = await self._checkpoint_store.run_redis_operation(
            lambda redis: redis.hmget(self.key, fields)
        )
        selected = []
        for record, (field, digest), stored in zip(records, fingerprints, previous):
            if stored != digest:
                self._pending[field] = digest
                selected.append(record)
        return selected

    async def commit(self) -> None:
        if not self._pending:
            return
        await self._checkpoint_store.run_redis_operation(
            lambda redis: redis.hset(self.key, mapping=self._pending)
        )
        self._pending.clear()
