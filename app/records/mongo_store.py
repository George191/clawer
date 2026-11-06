"""MongoDB record repository — merge, routing, and download/sync lifecycle.

功能
----
- 记录 CRUD：支持单条/批量保存、去重（按 record_id）
- 下载状态管理：pending → downloading → downloaded / no_assets，异常返回 pending
- 同步状态管理：pending → synced
- 查询接口：按 sample_id / download_status 筛选记录
- 索引自动创建：record_id 唯一索引、download_status/sync_status 查询索引
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from time import monotonic
from typing import Any

from bson.codec_options import CodecOptions
from pymongo import UpdateOne

from app.config.settings import settings
from app.logger import get_logger
from app.records.adapters import get_record_adapter
from app.records.base import RecordStore
from app.records.collection_routes import collection_route_store
from app.records.download_resources import (
    apply_record_updates,
    decode_manifest,
    prepare_download_state,
    preserve_resource_removals,
)
from app.records.field_names import normalize_record_fields
from app.records.values import record_values_equal, require_canonical_record
from app.storage.mongo_client import MongoClient
from app.utils.record_id import resolve_record_id
from app.utils.runtime_control import workspace_search_params

logger = get_logger(__name__)

_DOWNLOAD_META_FIELDS = frozenset({
    "download_status", "download_version", "download_attempts", "download_inputs", "downloaded_inputs", "download_plan",
    "download_claim_token", "download_claimed_at", "download_retry_at", "download_error",
})


def _field_changes(before: Any, after: Any, prefix: str = "") -> dict[str, Any]:
    """Write only changed leaves, keeping concurrently updated sibling fields intact."""
    if record_values_equal(before, after):
        return {}
    if isinstance(before, dict) and isinstance(after, dict):
        result = {}
        for key, value in after.items():
            path = f"{prefix}.{key}" if prefix else key
            result.update(_field_changes(before.get(key), value, path))
        return result
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        result = {}
        for index, value in enumerate(after):
            result.update(_field_changes(before[index], value, f"{prefix}.{index}"))
        return result
    return {prefix: after}


def _record_update(
    existing: dict[str, Any], record: dict[str, Any], resources_changed: bool, *, compare_version: bool = False,
) -> UpdateOne:
    """Keep download-owned fields server-side; explicitly invalidate changed resources."""
    old_meta, meta = existing.get("_meta") or {}, record["_meta"]
    business = {key: value for key, value in record.items() if key not in {"_id", "_meta", "assets"}}
    previous = {key: value for key, value in existing.items() if key not in {"_id", "_meta", "assets"}}
    fields = _field_changes(previous, business)
    for key, value in meta.items():
        if key not in _DOWNLOAD_META_FIELDS | {"updated_at"} and not record_values_equal(old_meta.get(key), value):
            fields[f"_meta.{key}"] = value
    if old_meta.get("write_token") != meta.get("write_token"):
        fields["_meta.sync_status"] = meta["sync_status"]
    update: dict[str, Any] = {"$set": fields}
    if meta.get("updated_at") is not None:
        update["$max"] = {"_meta.updated_at": meta["updated_at"]}
    if resources_changed:
        desired = decode_manifest(meta.get("download_inputs"))
        previous_inputs = decode_manifest(old_meta.get("download_inputs"))
        verified = decode_manifest(old_meta.get("downloaded_inputs"))
        invalid = {key for key, value in previous_inputs.items() if desired.get(key) != value}
        invalid.update(key for key in desired if key not in previous_inputs)
        invalid.update(key for key in verified if key not in desired)
        fields.update({
            "_meta.download_status": "pending", "_meta.download_inputs": meta["download_inputs"],
            "_meta.download_plan": meta["download_plan"],
        })
        if "download_inputs" not in old_meta:
            # Legacy files have no provable source identity and are verified once.
            invalid = {"assets"}
            fields["_meta.downloaded_inputs"] = []
        elif invalid:
            update["$pull"] = {"_meta.downloaded_inputs": {"asset_key": {"$in": sorted(invalid)}}}
        paths = []
        for path in sorted(invalid, key=len):
            if not any(path == parent or path.startswith(parent + ".") for parent in paths):
                paths.append(path)
        update["$unset"] = dict.fromkeys([
            *paths, "_meta.download_claim_token", "_meta.download_claimed_at", "_meta.download_error",
            "_meta.download_retry_at",
        ], "")
    if not fields:
        update.pop("$set")
    query = {"_meta.record_id": meta["record_id"]}
    if resources_changed or compare_version:
        query.update({"_meta.write_token": old_meta.get("write_token"),
                      "_meta.download_inputs": old_meta.get("download_inputs")})
    return UpdateOne(query, update)


def _ready_to_sync_filter() -> dict[str, Any]:
    """Select unsynced records independently of download status."""
    return {"_meta.sync_status": {"$ne": "synced"}}


def _pending_before(before: datetime | None) -> dict[str, Any]:
    if before is None:
        return {}
    return {"_meta.updated_at": {"$lte": before}}


class MongoRecordStore(MongoClient, RecordStore):
    def __init__(self) -> None:
        super().__init__()
        self._initialized_collections: set[str] = set()
        self._download_templates: dict[str, Any] = {}
        self._download_templates_checked_at: dict[str, float] = {}
        self.last_save_stats = {
            "inserted": 0, "updated": 0, "unchanged": 0, "deleted": 0,
        }

    def _get_collection_name(self, template_name: str) -> str:
        return template_name


    async def _get_collection(self, template_name: str):
        name = await collection_route_store.resolve_collection_name(template_name)
        return await self._get_collection_by_name(name)

    async def _get_collection_by_name(
        self, collection_name: str, *, indexes: list[tuple[str, bool]] | None = None,
    ):
        collection = await super()._get_collection(collection_name)
        if indexes is not None:
            await self._ensure_indexes(collection, indexes)
            self._initialized_collections.add(collection_name)
            return collection
        if collection_name not in self._initialized_collections:
            await collection.create_index("_meta.record_id", unique=True)
            await collection.create_index("_meta.download_status")
            await collection.create_index("_meta.download_claimed_at")
            await collection.create_index("_meta.sync_status")
            await collection.create_index(
                [
                    ("_meta.download_status", 1),
                    ("_meta.updated_at", 1),
                    ("_id", 1),
                ]
            )
            self._initialized_collections.add(collection_name)
        return collection


    def _resolve_record_id(self, record: dict[str, Any]) -> str:
        return resolve_record_id(record)

    @staticmethod
    def _business_content(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in record.items()
            if key not in {"_id", "_meta", "data_type"}
        }

    @staticmethod
    def _build_record_with_meta(
        *,
        template_name: str,
        data_type: str,
        record_id: str,
        record: dict[str, Any],
        search_params: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any]:
        result = {
            **record,
            "_meta": {
                "template": template_name,
                "data_type": data_type,
                "record_id": record_id,
                "download_status": "pending",
                "sync_status": "pending",
                "search_params": search_params,
                "created_at": now,
                "updated_at": now,
            },
        }
        return result

    @staticmethod
    def _has_meaningful_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, list | dict | set | tuple):
            return len(value) > 0
        return True

    @classmethod
    def _merge_non_empty_fields(
        cls,
        existing: Any,
        incoming: Any,
    ) -> Any:
        if isinstance(existing, dict) and isinstance(incoming, dict):
            merged = dict(existing)
            for key, value in incoming.items():
                if key in merged:
                    merged[key] = cls._merge_non_empty_fields(merged[key], value)
                    continue
                if cls._has_meaningful_value(value):
                    merged[key] = value
            return merged
        if cls._has_meaningful_value(incoming):
            return incoming
        return existing

    @classmethod
    def _drop_none_values(cls, value: Any) -> Any:
        if isinstance(value, dict):
            cleaned: dict[str, Any] = {}
            for key, item in value.items():
                next_item = cls._drop_none_values(item)
                if not cls._has_meaningful_value(next_item):
                    continue
                cleaned[key] = next_item
            return cleaned
        if isinstance(value, list):
            cleaned_list = [cls._drop_none_values(item) for item in value]
            return [
                item for item in cleaned_list
                if cls._has_meaningful_value(item)
            ]
        return value

    async def save_record(
        self, template_name: str, data_type: str, dedup_fields: list[str], record: dict[str, Any],
        *, download_template: Any = None,
    ) -> str:
        ids = await self.save_records(template_name, data_type, dedup_fields, [record], download_template=download_template)
        return ids[0]

    async def _download_template(self, name: str, provided: Any = None) -> Any:
        """Reuse the crawler's template; resolve legacy direct writers once per store."""
        if provided is not None:
            self._download_templates[name] = provided
            self._download_templates_checked_at[name] = monotonic()
        if name not in self._download_templates or monotonic() - self._download_templates_checked_at.get(name, 0) >= settings.download_template_cache_seconds:
            from app.engine.template_loader import TemplateLoader

            released = await TemplateLoader().load_released(name, validate_params=False, load_adapter=False)
            self._download_templates[name] = released.template
            self._download_templates_checked_at[name] = monotonic()
        return self._download_templates[name]

    async def _merge_insert_collision(
        self, collection: Any, adapter: Any, route: dict[str, str], incoming: dict[str, Any], download_template: Any,
        sync_status: str | None = None,
    ) -> tuple[str, dict[str, Any], bool]:
        """Rebuild only a conflicting record; successful batch writes stay untouched."""
        record_id = incoming["_meta"]["record_id"]
        for _ in range(3):
            existing = await collection.find_one({"_meta.record_id": record_id})
            if existing is None:
                result = await collection.update_one(
                    {"_meta.record_id": record_id}, {"$setOnInsert": incoming}, upsert=True,
                )
                if result.upserted_id is not None:
                    return "inserted", incoming, adapter.is_deletion(None, incoming)
                continue
            merged = await adapter.finalize(adapter.merge(existing, incoming), incoming["_meta"].get("search_params", {}))
            merged = preserve_resource_removals(merged, incoming, download_template.download)
            old_meta, meta = existing.get("_meta") or {}, merged["_meta"]
            meta["created_at"] = old_meta.get("created_at", meta["created_at"])
            meta["download_status"] = old_meta.get("download_status", "pending")
            resources_changed = prepare_download_state(existing, merged, download_template, route["template_name"], route["data_type"])
            changed = resources_changed or not record_values_equal(self._business_content(existing), self._business_content(merged))
            if changed:
                meta["sync_status"] = "pending"
                meta["write_token"] = uuid.uuid4().hex
                for key in ("__workspace_task_id", "__workspace_run_id"):
                    if key not in incoming["_meta"].get("search_params", {}):
                        meta.setdefault("search_params", {}).pop(key, None)
            else:
                for key in ("sync_status", "updated_at", "write_token"):
                    if key in old_meta:
                        meta[key] = old_meta[key]
                    else:
                        meta.pop(key, None)
                for key in ("__workspace_task_id", "__workspace_run_id"):
                    params = meta.setdefault("search_params", {})
                    if key in old_meta.get("search_params", {}):
                        params[key] = old_meta["search_params"][key]
                    else:
                        params.pop(key, None)
            require_canonical_record(merged)
            if sync_status is not None:
                meta["sync_status"] = sync_status
            operation = _record_update(existing, merged, resources_changed, compare_version=True)
            result = await collection.update_one(operation._filter, operation._doc)
            if result.matched_count:
                return "updated" if changed else "unchanged", merged, adapter.is_deletion(existing, merged)
        raise RuntimeError("Record changed repeatedly while resolving a concurrent write")

    async def save_records(
        self, template_name: str, data_type: str, dedup_fields: list[str], records: list[dict[str, Any]],
        *, sync_status: str | None = None, download_template: Any = None,
    ) -> list[str]:
        if not records:
            return []
        records = [normalize_record_fields(record, data_source=template_name) for record in records]
        for record in records:
            require_canonical_record(record)

        route = await collection_route_store.resolve_for_write(
            self._get_collection_name(template_name), data_type,
        )
        adapter = await get_record_adapter(self, route, dedup_fields)
        download_template = await self._download_template(route["template_name"], download_template)
        collection = await self._get_collection_by_name(
            route["collection_name"], indexes=adapter.collection_indexes,
        )
        await self._ensure_indexes(collection, [(field, False) for field in adapter.extra_indexes])
        now = datetime.now(timezone.utc)
        records = await adapter.prepare_batch(records, now)
        prepared_records: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        pending_docs: dict[str, dict[str, Any]] = {}
        pending_search_params: dict[str, dict[str, Any]] = {}
        ids: list[str] = []

        for record in records:
            search_params = workspace_search_params(record.pop("_meta_search_params", None) or {})
            cleaned_record = adapter.clean(record)
            record_id = self._resolve_record_id(adapter.identity(cleaned_record))
            pending = pending_docs.get(record_id)
            if pending is not None:
                cleaned_record = adapter.clean(adapter.merge(pending, cleaned_record))
            pending_docs[record_id] = cleaned_record
            pending_params = pending_search_params.get(record_id)
            if pending_params is not None:
                merged_params = self._merge_non_empty_fields(pending_params, search_params)
                search_params = (
                    self._drop_none_values(merged_params)
                    if isinstance(merged_params, dict)
                    else search_params
                )
            pending_search_params[record_id] = search_params
            prepared_records.append((record_id, cleaned_record, search_params))
            ids.append(record_id)

        unique_ids = list(dict.fromkeys(ids))
        existing_docs: dict[str, dict[str, Any]] = {}
        cursor = collection.find({"_meta.record_id": {"$in": unique_ids}})
        async for doc in cursor:
            meta = doc.get("_meta", {})
            record_id = meta.get("record_id")
            if record_id:
                existing_docs[record_id] = doc
        inserted_count = 0
        updated_count = 0
        unchanged_count = 0
        deleted_count = 0
        run_stats: dict[tuple[str, str], dict[str, int]] = {}

        operations: list[UpdateOne] = []
        new_records: dict[int, tuple[dict[str, Any], bool]] = {}
        resource_writes: list[tuple[UpdateOne, dict[str, Any], dict[str, Any], bool]] = []
        conflicts: list[tuple[str, dict[str, Any], dict[str, Any], bool]] = []
        processed_ids: set[str] = set()
        for record_id, cleaned_record, search_params in reversed(prepared_records):
            if record_id in processed_ids:
                continue
            processed_ids.add(record_id)
            cleaned_record = pending_docs[record_id]
            search_params = pending_search_params[record_id]
            record_with_meta = self._build_record_with_meta(
                template_name=route["collection_name"],
                data_type=route["data_type"],
                record_id=record_id,
                record=cleaned_record,
                search_params=search_params,
                now=now,
            )
            record_with_meta["_meta"].update(adapter.metadata)
            existing = existing_docs.get(record_id)
            deletion = adapter.is_deletion(existing, cleaned_record)
            final_record = adapter.merge(existing or {}, record_with_meta)
            final_record = await adapter.finalize(final_record, search_params)
            final_record = preserve_resource_removals(final_record, cleaned_record, download_template.download)
            require_canonical_record(final_record)
            if existing:
                final_record["_meta"]["download_status"] = (existing.get("_meta") or {}).get("download_status", "pending")
            resources_changed = prepare_download_state(
                existing, final_record, download_template, route["template_name"], route["data_type"],
            )
            if existing:
                existing_meta = existing.get("_meta", {})
                final_record["_meta"]["created_at"] = existing_meta.get("created_at", now)
                business_changed = not record_values_equal(
                    self._business_content(existing), self._business_content(final_record),
                )
                search_params_changed = existing_meta.get(
                    "search_params", {}
                ) != final_record["_meta"].get("search_params", {})
                metadata_changed = any(
                    existing_meta.get(key) != final_record["_meta"].get(key)
                    for key in ("data_type", "template")
                )
                template_changed = existing_meta.get("template") != final_record["_meta"].get("template")
                if business_changed or template_changed or resources_changed:
                    # A write without a batch identity cannot inherit the old
                    # batch's attribution merely by merging existing metadata.
                    final_params = final_record["_meta"].get("search_params", {})
                    for key in ("__workspace_task_id", "__workspace_run_id"):
                        if key not in search_params:
                            final_params.pop(key, None)
                    final_record["_meta"]["sync_status"] = "pending"
                    final_record["_meta"]["updated_at"] = now
                    updated_count += 1
                else:
                    # Re-observing an unchanged record must not move unfinished
                    # downloads/syncs from their original execution batch.
                    final_params = final_record["_meta"].get("search_params", {})
                    existing_params = existing_meta.get("search_params", {})
                    for key in ("__workspace_task_id", "__workspace_run_id"):
                        if key in existing_params:
                            final_params[key] = existing_params[key]
                        else:
                            final_params.pop(key, None)
                    search_params_changed = existing_params != final_params
                    final_record["_meta"]["sync_status"] = existing_meta.get(
                        "sync_status", "pending"
                    )
                    final_record["_meta"]["updated_at"] = existing_meta.get(
                        "updated_at", now
                    )
                    unchanged_count += 1
                    if not metadata_changed and not search_params_changed and (
                        sync_status is None or final_record["_meta"]["sync_status"] == sync_status
                    ):
                        continue
            else:
                inserted_count += 1

            if not existing or business_changed or template_changed or resources_changed:
                # Mongo dates have millisecond precision; distinguish business
                # writes even when timestamps and download versions collide.
                final_record["_meta"]["write_token"] = uuid.uuid4().hex
                owner = final_record["_meta"].get("search_params", {})
                task_id = str(owner.get("__workspace_task_id") or "")
                run_id = str(owner.get("__workspace_run_id") or "")
                if task_id and run_id:
                    counts = run_stats.setdefault((task_id, run_id), {"inserted": 0, "updated": 0})
                    counts["updated" if existing else "inserted"] += 1
                    if deletion:
                        counts["deleted"] = counts.get("deleted", 0) + 1

            deleted_count += int(deletion)
            if sync_status is not None:
                final_record["_meta"]["sync_status"] = sync_status
            if existing:
                operation = _record_update(existing, final_record, resources_changed)
                if resources_changed:
                    resource_writes.append((operation, final_record, record_with_meta, deletion))
                else:
                    operations.append(operation)
            else:
                new_records[len(operations)] = (final_record, deletion)
                operations.append(UpdateOne(
                    {"_meta.record_id": record_id}, {"$setOnInsert": final_record}, upsert=True,
                ))

        if operations:
            result = await collection.bulk_write(operations, ordered=False)
            if new_records and isinstance(result.upserted_ids, dict):
                for index, (incoming, deletion) in new_records.items():
                    if index in result.upserted_ids:
                        continue
                    conflicts.append(("inserted", incoming, incoming, deletion))
        for operation, planned, incoming, deletion in resource_writes:
            result = await collection.update_one(operation._filter, operation._doc)
            if not result.matched_count:
                conflicts.append(("updated", planned, incoming, deletion))
        for planned_kind, planned, incoming, deletion in conflicts:
            inserted_count -= int(planned_kind == "inserted")
            updated_count -= int(planned_kind == "updated")
            deleted_count -= int(deletion)
            owner = planned["_meta"].get("search_params", {})
            identity = (str(owner.get("__workspace_task_id") or ""), str(owner.get("__workspace_run_id") or ""))
            if identity in run_stats:
                run_stats[identity][planned_kind] -= 1
                if deletion:
                    run_stats[identity]["deleted"] -= 1
            kind, persisted, deleted = await self._merge_insert_collision(
                collection, adapter, route, incoming, download_template, sync_status,
            )
            inserted_count += int(kind == "inserted")
            updated_count += int(kind == "updated")
            unchanged_count += int(kind == "unchanged")
            deleted_count += int(deleted)
            owner = persisted["_meta"].get("search_params", {})
            identity = (str(owner.get("__workspace_task_id") or ""), str(owner.get("__workspace_run_id") or ""))
            if all(identity) and kind != "unchanged":
                counts = run_stats.setdefault(identity, {"inserted": 0, "updated": 0})
                counts[kind] += 1
                if deleted:
                    counts["deleted"] = counts.get("deleted", 0) + 1
        await adapter.after_save({owner: counts for owner, counts in run_stats.items() if any(counts.values())})
        self.last_save_stats = {
            "inserted": inserted_count,
            "updated": updated_count,
            "unchanged": unchanged_count,
            "deleted": deleted_count,
        }
        return ids

    async def update_file_status(
        self,
        template_name: str,
        record_id: str,
        download_status: str,
        claim_token: str | None = None,
    ) -> bool:
        collection = await self._get_collection(template_name)
        query = {"_meta.record_id": record_id}
        if claim_token:
            query["_meta.download_claim_token"] = claim_token
        result = await collection.update_one(
            query,
            {
                "$set": {
                    "_meta.download_status": download_status,
                    "_meta.sync_status": "pending",
                    "_meta.updated_at": datetime.now(timezone.utc),
                },
                "$inc": {"_meta.download_version": 1},
                "$unset": {
                    "_meta.download_claim_token": "",
                },
            },
        )
        logger.debug("Updated file_status for %s: %s", record_id, download_status)
        return bool(result.matched_count)


    async def update_record_fields(
        self,
        template_name: str,
        record_id: str,
        updates: dict[str, Any],
    ) -> None:
        collection = await self._get_collection(template_name)
        for _ in range(3):
            existing = await collection.find_one({"_meta.record_id": record_id})
            if existing is None:
                return
            meta = existing.get("_meta") or {}
            route = await collection_route_store.resolve_for_write(template_name, meta.get("data_type"))
            template = await self._download_template(route["template_name"])
            record = apply_record_updates(existing, updates)
            resources_changed = prepare_download_state(existing, record, template, route["template_name"], route["data_type"])
            set_fields = {
                **updates,
                "_meta.updated_at": datetime.now(timezone.utc),
                "_meta.sync_status": "pending",
                "_meta.write_token": uuid.uuid4().hex,
                "_meta.download_inputs": record["_meta"]["download_inputs"],
                "_meta.download_plan": record["_meta"]["download_plan"],
            }
            unset_fields = {}
            if resources_changed:
                set_fields.update({
                    "_meta.download_status": "pending",
                    "_meta.downloaded_inputs": record["_meta"]["downloaded_inputs"],
                })
                if "assets" in record:
                    set_fields = {key: value for key, value in set_fields.items() if not key.startswith("assets.")}
                    set_fields["assets"] = record["assets"]
                unset_fields = dict.fromkeys((
                    "_meta.download_claim_token", "_meta.download_claimed_at", "_meta.download_error",
                    "_meta.download_retry_at",
                ), "")
            query = {
                "_meta.record_id": record_id, "_meta.write_token": meta.get("write_token"),
                "_meta.download_version": meta.get("download_version"),
                "_meta.download_claim_token": meta.get("download_claim_token"),
            }
            update = {"$set": set_fields}
            if unset_fields:
                update["$unset"] = unset_fields
            result = await collection.update_one(query, update)
            if result.matched_count:
                return
        raise RuntimeError("Record changed concurrently while updating resource inputs")

    async def update_download_result(
        self,
        template_name: str,
        record_id: str,
        updates: dict[str, Any],
        download_status: str,
        unset_fields: set[str] | None = None,
        claim_token: str | None = None,
    ) -> bool:
        """Persist download results and mark the new version pending sync atomically."""
        collection = await self._get_collection(template_name)
        fields_to_unset = {
            "_meta.download_claim_token": "",
            "_meta.download_claimed_at": "",
            **dict.fromkeys((
                field for field in ("_meta.download_retry_at", "_meta.download_error")
                if field not in updates
            ), ""),
            **dict.fromkeys(unset_fields or (), ""),
        }
        query = {"_meta.record_id": record_id}
        if claim_token:
            query["_meta.download_claim_token"] = claim_token
        result = await collection.update_one(
            query,
            {
                "$set": {
                    **updates,
                    "_meta.download_status": download_status,
                    "_meta.sync_status": "pending",
                    "_meta.updated_at": datetime.now(timezone.utc),
                },
                "$inc": {"_meta.download_version": 1},
                "$unset": fields_to_unset,
            },
        )
        logger.debug(
            "Updated download result for %s: status=%s fields=%d",
            record_id,
            download_status,
            len(updates),
        )
        return bool(result.matched_count)

    async def update_sync_status(
        self,
        template_name: str,
        data_type: str,
        record_id: str,
        sync_status: str,
        *,
        updated_at: datetime | None = None,
        download_version: int | None = None,
        write_token: str | None = None,
    ) -> bool:
        # Acknowledging a sync must not wait for index creation either.
        await self._ensure_connection()
        collection_name = await collection_route_store.resolve_collection_name(template_name, data_type)
        collection = self._db[collection_name]
        query = {"_meta.record_id": record_id}
        set_fields: dict[str, Any] = {"_meta.sync_status": sync_status}
        if sync_status == "synced":
            query["_meta.sync_status"] = {"$ne": "synced"}
            query["_meta.write_token"] = write_token
            # Acknowledge only the version sent to Kafka, never a concurrent update.
            if updated_at is not None:
                query["_meta.updated_at"] = updated_at
            if download_version is not None:
                query["_meta.download_version"] = (
                    {"$in": [None, 0]} if download_version == 0 else download_version
                )
            set_fields["_meta.synced_at"] = updated_at or datetime.now(timezone.utc)
        result = await collection.update_one(
            query,
            {"$set": set_fields},
        )
        logger.debug("Updated sync_status for %s: %s", record_id, sync_status)
        return bool(result.matched_count)

    async def get_unsynced_records(
        self,
        template_name: str,
        data_type: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        collection_name = await collection_route_store.resolve_collection_name(template_name, data_type)
        collection = await self._get_collection_by_name(collection_name)
        cursor = collection.find(_ready_to_sync_filter()).limit(limit)
        results: list[dict[str, Any]] = []
        async for doc in cursor:
            doc.pop("_id", None)
            results.append(doc)
        return results

    async def get_ready_to_sync(
        self,
        template_name: str | None = None,
        data_type: str | None = None,
        limit: int = 100,
        before: datetime | None = None,
    ) -> list[dict[str, Any]]:
        await self._ensure_connection()

        filter_query = {**_ready_to_sync_filter(), **_pending_before(before)}

        if template_name:
            # The syncer must not block its hot path on create_index calls.
            coll_name = await collection_route_store.resolve_collection_name(template_name, data_type)
            collection = self._db[coll_name].with_options(codec_options=CodecOptions(tz_aware=True))
            cursor = collection.find(filter_query).limit(limit)
            results: list[dict[str, Any]] = []
            async for doc in cursor:
                doc.pop("_id", None)
                results.append(doc)
            return results

        # 跨所有集合查询
        results = []
        for coll_name in await self._db.list_collection_names():
            collection = self._db[coll_name].with_options(codec_options=CodecOptions(tz_aware=True))
            cursor = collection.find(filter_query).limit(limit - len(results))
            async for doc in cursor:
                doc.pop("_id", None)
                results.append(doc)
                if len(results) >= limit:
                    return results
        return results

    async def get_pending_downloads(
        self,
        template_name: str | None = None,
        limit: int = 50,
        balanced: bool = True,
        before: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """获取待下载记录。

        Args:
            template_name: 指定模板名，None 表示扫描所有模板
            limit: 最大返回记录数
            balanced: 是否均衡分配（轮询各集合，避免单集合独占批次）

        Returns:
            待下载记录列表，每条记录包含 _meta 字段（含 template）
        """
        await self._ensure_connection()

        filter_query = {**self._download_claim_filter(), **_pending_before(before)}

        if template_name:
            collection = await self._get_collection(template_name)
            return await self._claim_pending_downloads_from_collection(
                collection, filter_query, limit,
            )

        # 全库扫描
        coll_names = await self._db.list_collection_names()
        if not coll_names:
            return []

        if balanced:
            return await self._balanced_pending_downloads(
                coll_names, filter_query, limit,
            )
        else:
            # 贪婪模式：第一个集合填充整个批次
            results = []
            for coll_name in coll_names:
                collection = self._db[coll_name]
                claimed = await self._claim_pending_downloads_from_collection(
                    collection, filter_query, limit - len(results),
                )
                results.extend(claimed)
                if len(results) >= limit:
                    return results
            return results

    async def has_unfinished_downloads(self, template_name: str) -> bool:
        """Check for queued or claimed work without excluding future retries."""
        collection = await self._get_collection(template_name)
        return await collection.find_one(
            {"_meta.download_status": {"$in": ["pending", "downloading"]}},
            {"_id": 1},
        ) is not None

    @staticmethod
    def _download_claim_filter() -> dict[str, Any]:
        return {
            "_meta.download_status": "pending",
            "$or": [
                {"_meta.download_retry_at": None},
                {"_meta.download_retry_at": {"$lte": datetime.now(timezone.utc)}},
            ],
        }

    async def claim_download(self, record: dict[str, Any]) -> dict[str, Any] | None:
        """Claim a delivered snapshot only while its persisted version is current."""
        meta = record.get("_meta") or {}
        name = meta.get("template")
        if not name or not meta.get("record_id"):
            return None
        collection = await self._get_collection(name)
        query = {
            "_meta.record_id": meta["record_id"],
            "_meta.write_token": meta.get("write_token"),
            "_meta.updated_at": meta.get("updated_at"),
            "_meta.download_version": (
                meta["download_version"] if meta.get("download_version")
                else {"$in": [None, 0]}
            ),
            **self._download_claim_filter(),
        }
        now = datetime.now(timezone.utc)
        token = uuid.uuid4().hex
        result = await collection.update_one(query, {
            "$set": {
                "_meta.download_status": "downloading",
                "_meta.download_claimed_at": now,
                "_meta.download_claim_token": token,
            },
            "$inc": {"_meta.download_attempts": 1},
        })
        if not result.matched_count:
            return None
        return {**record, "_meta": {
            **meta,
            "download_status": "downloading",
            "download_claimed_at": now,
            "download_claim_token": token,
            "download_attempts": int(meta.get("download_attempts") or 0) + 1,
        }}

    async def renew_download_claim(
        self, template_name: str, record_id: str, claim_token: str,
    ) -> bool:
        collection = await self._get_collection(template_name)
        result = await collection.update_one(
            {"_meta.record_id": record_id, "_meta.download_claim_token": claim_token,
             "_meta.download_status": "downloading"},
            {"$set": {"_meta.download_claimed_at": datetime.now(timezone.utc)}},
        )
        return bool(result.matched_count)

    async def release_download_claim(
        self, template_name: str, record_id: str, claim_token: str,
    ) -> None:
        collection = await self._get_collection(template_name)
        await collection.update_one(
            {"_meta.record_id": record_id, "_meta.download_claim_token": claim_token,
             "_meta.download_status": "downloading"},
            {"$set": {"_meta.download_status": "pending"},
             "$unset": {"_meta.download_claim_token": "", "_meta.download_claimed_at": ""}},
        )

    async def recover_stale_downloads(
        self, template_name: str, before: datetime,
    ) -> int:
        """Return claims without a recent worker heartbeat to the pending backlog."""
        await self._ensure_connection()
        name = await collection_route_store.resolve_collection_name(template_name)
        result = await self._db[name].update_many(
            {"_meta.download_status": "downloading",
             "_meta.download_claimed_at": {"$lte": before}},
            {"$set": {"_meta.download_status": "pending"},
             "$unset": {"_meta.download_claim_token": "", "_meta.download_claimed_at": ""}},
        )
        return result.modified_count

    async def _claim_pending_downloads_from_collection(
        self,
        collection: Any,
        filter_query: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        batch_limit = max(0, limit)
        if batch_limit == 0:
            return []

        candidates = await collection.find(
            filter_query,
            {"_id": 1},
        ).sort([
            ("_meta.updated_at", 1),
            ("_id", 1),
        ]).limit(batch_limit).to_list(length=batch_limit)
        if not candidates:
            return []

        now = datetime.now(timezone.utc)
        claim_token = uuid.uuid4().hex
        candidate_ids = [doc["_id"] for doc in candidates]

        await collection.update_many(
            {
                "_id": {"$in": candidate_ids},
                **filter_query,
            },
            {
                "$set": {
                    "_meta.download_status": "downloading",
                    "_meta.download_claimed_at": now,
                    "_meta.download_claim_token": claim_token,
                },
                "$inc": {"_meta.download_attempts": 1},
            },
        )

        claimed_docs = await collection.find(
            {
                "_id": {"$in": candidate_ids},
                "_meta.download_claim_token": claim_token,
            }
        ).sort([
            ("_meta.updated_at", 1),
            ("_id", 1),
        ]).to_list(length=batch_limit)

        results: list[dict[str, Any]] = []
        for doc in claimed_docs:
            doc.pop("_id", None)
            results.append(doc)
        return results

    async def _balanced_pending_downloads(
        self,
        coll_names: list[str],
        filter_query: dict[str, Any],
        limit: int,
    ) -> list[dict[str, Any]]:
        """均衡轮询各集合，避免某个集合的挂起记录独占整个批次。"""
        results = []
        # 每轮从每个集合取若干条（按集合数均分 + 至少 1 条）
        per_coll = max(1, limit // len(coll_names))

        # 记录每个集合的游标，按需取
        for coll_name in coll_names:
            if len(results) >= limit:
                break
            collection = self._db[coll_name]
            claimed = await self._claim_pending_downloads_from_collection(
                collection, filter_query, min(per_coll, limit - len(results)),
            )
            results.extend(claimed)

        return results

    async def get_collection_stats(
        self, template_name: str | None = None, *, missing_ok: bool = False,
    ) -> list[dict[str, Any]]:
        """获取所有集合的概览统计。

        missing_ok 为 True 时，指定集合不存在则返回空列表。

        Returns:
            [{"name": "planet", "total": 100, "pending_download": 5, "downloaded": 80, ...}, ...]
        """
        await self._ensure_connection()
        stats = []
        if template_name:
            template_name = await collection_route_store.resolve_collection_name(template_name)
            all_colls = await self._db.list_collection_names()
            if template_name not in all_colls:
                if missing_ok:
                    return []
                raise ValueError(
                    f"Collection '{template_name}' not found in MongoDB. "
                    f"Available: {', '.join(sorted(all_colls))}"
                )
            coll_names = [template_name]
        else:
            coll_names = await self._db.list_collection_names()
        for coll_name in coll_names:
            collection = self._db[coll_name]
            total = await collection.count_documents({})
            pending = await collection.count_documents({
                "_meta.download_status": {"$in": ["pending", "downloading"]},
            })
            downloaded = await collection.count_documents({
                "_meta.download_status": "downloaded",
            })
            no_assets = await collection.count_documents({
                "_meta.download_status": "no_assets",
            })
            failed = await collection.count_documents({
                "_meta.download_status": "failed",
            })
            stats.append({
                "name": coll_name,
                "total": total,
                "pending_download": pending,
                "downloaded": downloaded,
                "no_assets": no_assets,
                "failed": failed,
            })
        return stats

