"""Source-specific record rules used by the generic MongoDB storage backend."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.records.collection_routes import collection_route_store
from app.utils.path import get_nested_value
from app.utils.sec_financial_facts import (
    FACT_DEDUP_FIELDS,
    financial_fact_identity,
    iter_sec_financial_facts,
)
from app.utils.twitter_accounts import split_twitter_accounts, upsert_twitter_accounts

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
        return self.storage._drop_none_values(record)

    def identity(self, record: dict[str, Any]) -> dict[str, Any]:
        return {field: get_nested_value(record, field) for field in self.dedup_fields}

    def merge(
        self, existing: dict[str, Any], incoming: dict[str, Any],
    ) -> dict[str, Any]:
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


class TwitterRecordAdapter(RecordAdapter):
    metadata = {"data_source": "twitter"}


class TwitterTweetAdapter(TwitterRecordAdapter):
    extra_indexes = ("author_id",)

    async def prepare_batch(
        self, records: list[dict[str, Any]], now: datetime,
    ) -> list[dict[str, Any]]:
        route = await collection_route_store.resolve_for_write(
            self.route["template_name"], "account",
        )
        collection = await self.storage._get_collection_by_name(route["collection_name"])
        await self.storage._ensure_indexes(collection, [("id", True)])
        stripped_records = []
        profiles: dict[str, dict[str, Any]] = {}
        for record in records:
            stripped, accounts = split_twitter_accounts(record)
            stripped_records.append(stripped)
            for account in accounts:
                profiles.setdefault(account["id"], {}).update(account)
        await upsert_twitter_accounts(collection, list(profiles.values()), now)
        return stripped_records

    async def finalize(
        self, record: dict[str, Any], search_params: dict[str, Any],
    ) -> dict[str, Any]:
        stripped, _ = split_twitter_accounts(record)
        return await super().finalize(stripped, search_params)


class SamSnapshotAdapter(RecordAdapter):
    def __init__(
        self, storage: MongoRecordStore, route: dict[str, str], dedup_fields: list[str],
    ) -> None:
        super().__init__(storage, route, dedup_fields)
        self._snapshots: dict[str, dict[str, Any]] = {}

    def clean(self, record: dict[str, Any]) -> dict[str, Any]:
        cleaned = dict(record)
        if cleaned.get("deleted") is not True:
            record_id = self.storage._resolve_record_id(self.identity(cleaned))
            self._snapshots[record_id] = cleaned
        return cleaned

    def merge(
        self, existing: dict[str, Any], incoming: dict[str, Any],
    ) -> dict[str, Any]:
        if "_meta" not in incoming:
            return incoming
        merged = dict(incoming)
        if incoming.get("deleted") is True:
            record_id = incoming["_meta"]["record_id"]
            snapshot = self._snapshots.get(record_id)
            if snapshot is None and existing:
                snapshot = self.storage._business_content(existing)
            if snapshot is not None:
                merged = {**snapshot, "deleted": True, "_meta": incoming["_meta"]}
        merged["_meta"] = self.storage._merge_non_empty_fields(
            existing.get("_meta", {}), incoming["_meta"],
        )
        return merged

    def is_deletion(
        self, existing: dict[str, Any] | None, incoming: dict[str, Any],
    ) -> bool:
        return incoming.get("deleted") is True and (
            not existing or existing.get("deleted") is not True
        )


class SamOpportunityAdapter(SamSnapshotAdapter):
    @classmethod
    def _merge_source_object(cls, existing: Any, incoming: Any) -> Any:
        if not isinstance(existing, dict) or not isinstance(incoming, dict):
            return incoming
        return {
            **existing,
            **{key: cls._merge_source_object(existing.get(key), value) for key, value in incoming.items()},
        }

    def merge(
        self, existing: dict[str, Any], incoming: dict[str, Any],
    ) -> dict[str, Any]:
        merged = super().merge(existing, incoming)
        source = incoming.get("source_data") or {}
        if incoming.get("deleted") is True or not isinstance(source, dict):
            return merged
        from_csv = "NoticeId" in source
        if not from_csv and "noticeId" not in source:
            return merged
        previous_source = existing.get("source_data") or {}
        merged["source_data"] = {**previous_source, **source}
        for field in ("award", "place_of_performance"):
            if field in incoming:
                merged[field] = self._merge_source_object(existing.get(field), incoming[field])
        if from_csv:
            # The public CSV has no attachment or description-endpoint fields.
            for field in ("attachments", "description_url"):
                if field in existing:
                    merged[field] = existing[field]
            if "noticeId" in previous_source and not incoming.get("content"):
                merged["content"] = existing.get("content", "")
        else:
            for field in (
                "base_type", "archive_type", "archive_date", "set_aside_code", "set_aside",
                "organization_type", "office_address", "additional_info_url",
            ):
                if field in existing and field not in incoming:
                    merged[field] = existing[field]
            # API list requests may intentionally skip the separate body request.
            if not incoming.get("content"):
                merged["content"] = existing.get("content", "")
        merged["attachments_available"] = bool(merged.get("attachments"))
        return merged


class SamAwardAdapter(SamSnapshotAdapter):
    @staticmethod
    def _identifiers(record: dict[str, Any]) -> list[str] | None:
        contract = (record.get("source_data") or {}).get("contractId")
        if not contract:
            return None
        values = [
            (contract.get("subtier") or {}).get("code"),
            contract.get("piid"),
            contract.get("modificationNumber"),
            str(contract.get("transactionNumber") or "0").strip() or "0",
            (contract.get("referencedIDVSubtier") or {}).get("code"),
            contract.get("referencedIDVPiid"),
            contract.get("referencedIDVModificationNumber"),
        ]
        return [str(value if value is not None else "").strip() for value in values]

    @staticmethod
    def _format_id(identifiers: list[str]) -> str:
        return "sam_gov_awards:" + json.dumps(
            identifiers, ensure_ascii=False, separators=(",", ":"),
        )

    def clean(self, record: dict[str, Any]) -> dict[str, Any]:
        identifiers = self._identifiers(record)
        if identifiers is not None:
            record = {**record, "id": self._format_id([identifiers[i] for i in (0, 1, 4, 5)])}
        return super().clean(record)

    def identity(self, record: dict[str, Any]) -> dict[str, Any]:
        identifiers = self._identifiers(record)
        if identifiers is None:
            return super().identity(record)
        # Keep the existing transaction key so revisions remain separate records.
        return {"id": self._format_id(identifiers)}


class SecCompanyAdapter(RecordAdapter):
    def clean(self, record: dict[str, Any]) -> dict[str, Any]:
        cleaned = super().clean(record)
        if "facts" in record:
            cleaned["facts"] = record["facts"]
        return cleaned

    async def finalize(
        self, record: dict[str, Any], search_params: dict[str, Any],
    ) -> dict[str, Any]:
        if "facts" in record:
            from app.records.mongo_store import MongoRecordStore

            fact_storage = MongoRecordStore()
            fact_storage._client = self.storage._client
            fact_storage._db = self.storage._db
            fact_storage._initialized_collections = self.storage._initialized_collections
            fact_storage._initialized_indexes = self.storage._initialized_indexes
            batch: list[dict[str, Any]] = []
            fact_source = {**record, "_meta": {"search_params": search_params}}
            for fact in iter_sec_financial_facts(fact_source):
                batch.append(fact)
                if len(batch) == 500:
                    await fact_storage.save_records(
                        self.route["template_name"], "financial_fact",
                        list(FACT_DEDUP_FIELDS), batch,
                    )
                    batch = []
            if batch:
                await fact_storage.save_records(
                    self.route["template_name"], "financial_fact",
                    list(FACT_DEDUP_FIELDS), batch,
                )
            record.pop("facts")
        return await super().finalize(record, search_params)


class SecFinancialFactAdapter(RecordAdapter):
    metadata = {"data_source": "sec_edgar_company", "download_status": "no_assets"}
    collection_indexes = [("_meta.record_id", True), ("cik", False), ("_meta.sync_status", False)]

    def identity(self, record: dict[str, Any]) -> dict[str, Any]:
        return financial_fact_identity(record)


_ADAPTERS: dict[tuple[str, str | None], type[RecordAdapter]] = {
    ("twitter", "tweet"): TwitterTweetAdapter,
    ("twitter", "account"): TwitterRecordAdapter,
    ("sec_edgar", "company"): SecCompanyAdapter,
    ("sec_edgar", "financial_fact"): SecFinancialFactAdapter,
    ("sam_gov_opportunities", None): SamOpportunityAdapter,
    ("sam_gov_awards", None): SamAwardAdapter,
}


def get_record_adapter(
    storage: MongoRecordStore, route: dict[str, str], dedup_fields: list[str],
) -> RecordAdapter:
    adapter = _ADAPTERS.get((route["template_name"], route["data_type"]))
    if adapter is None:
        adapter = _ADAPTERS.get((route["template_name"], None), RecordAdapter)
    return adapter(storage, route, dedup_fields)
