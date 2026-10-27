"""Generic record lifecycle interface and published extension selection."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.utils.path import get_nested_value

if TYPE_CHECKING:
    from app.records.mongo_store import MongoRecordStore


class RecordAdapter:
    metadata: dict[str, Any] = {}
    extra_indexes: tuple[str, ...] = ()
    collection_indexes: list[tuple[str, bool]] | None = None

    def __init__(
        self, storage: MongoRecordStore, route: dict[str, str], dedup_fields: list[str],
    ) -> None:
        self.storage = storage
        self.route = route
        self.dedup_fields = dedup_fields

    async def prepare_batch(
        self, records: list[dict[str, Any]], now: datetime,
    ) -> list[dict[str, Any]]:
        return records

    def clean(self, record: dict[str, Any]) -> dict[str, Any]:
        return dict(record)

    def identity(self, record: dict[str, Any]) -> dict[str, Any]:
        return {field: get_nested_value(record, field) for field in self.dedup_fields}

    def merge(
        self, existing: dict[str, Any], incoming: dict[str, Any],
    ) -> dict[str, Any]:
        if not existing:
            return dict(incoming)
        return self.storage._merge_non_empty_fields(existing, incoming)

    async def finalize(
        self, record: dict[str, Any], search_params: dict[str, Any],
    ) -> dict[str, Any]:
        return self.clean(record)

    def is_deletion(
        self, existing: dict[str, Any] | None, incoming: dict[str, Any],
    ) -> bool:
        return False

    async def after_save(self, run_stats: dict[tuple[str, str], dict[str, int]]) -> None:
        if not run_stats:
            return
        from app.web.services.ai_collect_store import ai_collect_store

        for (task_id, run_id), counts in run_stats.items():
            await ai_collect_store.increment_run_stats(task_id, run_id, **counts)


async def get_record_adapter(
    storage: MongoRecordStore, route: dict[str, str], dedup_fields: list[str],
) -> RecordAdapter:
    from app.adapters.record_rules import BUILTIN_RECORD_ADAPTERS
    from app.adapters.runtime import get_source_module

    module = await get_source_module(route["collection_name"])
    adapter = getattr(module, "RECORD_ADAPTERS", {}).get(route["data_type"])
    if adapter is None:
        adapter = BUILTIN_RECORD_ADAPTERS.get((route["template_name"], route["data_type"]))
    if adapter is None:
        adapter = BUILTIN_RECORD_ADAPTERS.get((route["template_name"], None), RecordAdapter)
    if not isinstance(adapter, type) or not issubclass(adapter, RecordAdapter):
        raise ValueError("Published RECORD_ADAPTERS values must extend RecordAdapter")
    return adapter(storage, route, dedup_fields)
