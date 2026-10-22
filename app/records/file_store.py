"""JSON-backed record persistence and download/sync lifecycle state."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.logger import get_logger
from app.records.base import RecordStore
from app.records.field_names import normalize_record_fields
from app.records.values import record_values_equal, require_canonical_record
from app.storage.file_client import FileClient
from app.utils.path import get_nested_value

logger = get_logger(__name__)


class FileRecordStore(FileClient, RecordStore):
    def __init__(self, base_dir: str | None = None) -> None:
        super().__init__(base_dir)
        self.last_save_stats = {
            "inserted": 0, "updated": 0, "unchanged": 0, "deleted": 0,
        }

    def _get_record_dir(self, template_name: str, data_type: str) -> Path:
        record_dir = self._base_dir / template_name / data_type
        record_dir.mkdir(parents=True, exist_ok=True)
        return record_dir

    def _resolve_legacy_record_id(self, record: dict[str, Any]) -> str:
        for key in ("id", "uid", "contract_no", "patent_id", "title"):
            if key in record and record[key]:
                value = str(record[key]).strip()
                safe_value = "".join(c if c.isalnum() or c in "-_" else "_" for c in value)
                return safe_value[:200]
        content = json.dumps(record, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(content.encode()).hexdigest()

    def _resolve_record_id(
        self,
        record: dict[str, Any],
        dedup_fields: list[str] | None = None,
    ) -> str:
        if dedup_fields:
            identity = {
                field: get_nested_value(record, field) for field in dedup_fields
            }
            content = json.dumps(identity, sort_keys=True, ensure_ascii=False)
            return hashlib.md5(content.encode()).hexdigest()
        return self._resolve_legacy_record_id(record)

    def _record_path(
        self,
        record_dir: Path,
        record: dict[str, Any],
        dedup_fields: list[str],
    ) -> Path:
        path = record_dir / f"{self._resolve_record_id(record, dedup_fields)}.json"
        legacy_path = record_dir / f"{self._resolve_legacy_record_id(record)}.json"
        if legacy_path != path and legacy_path.exists() and not path.exists():
            return legacy_path
        return path

    @staticmethod
    def _business_content(record: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in record.items()
            if key not in {"_meta", "_meta_search_params", "data_type"}
        }

    async def save_record(
        self,
        template_name: str,
        data_type: str,
        dedup_fields: list[str],
        record: dict[str, Any],
    ) -> str:
        record = normalize_record_fields(record, data_source=template_name)
        require_canonical_record(record)
        record_dir = self._get_record_dir(template_name, data_type)
        file_path = self._record_path(record_dir, record, dedup_fields)
        record_id = file_path.stem

        record = dict(record)
        search_params = record.pop("_meta_search_params", None) or {}

        existing: dict[str, Any] = {}
        if file_path.exists():
            existing = self.read_json(file_path)
        existing_meta = dict(existing.get("_meta") or {})
        existing_params = dict(existing_meta.get("search_params") or {})
        existing_params.update(search_params)

        record_with_meta = {
            **record,
            "_meta": {
                **existing_meta,
                "template": template_name,
                "data_type": data_type,
                "record_id": record_id,
                "download_status": existing_meta.get("download_status", "pending"),
                "sync_status": (
                    "pending"
                    if existing and not record_values_equal(self._business_content(existing), self._business_content(record))
                    else existing_meta.get("sync_status", "pending")
                ),
                "search_params": existing_params,
            },
        }
        require_canonical_record(record_with_meta)
        if not record_values_equal(record_with_meta, existing):
            self.write_json(file_path, record_with_meta)
        logger.debug("Saved record: %s", file_path)
        return str(file_path)

    async def save_records(
        self,
        template_name: str,
        data_type: str,
        dedup_fields: list[str],
        records: list[dict[str, Any]],
    ) -> list[str]:
        records = [normalize_record_fields(record, data_source=template_name) for record in records]
        for record in records:
            require_canonical_record(record)
        record_dir = self._get_record_dir(template_name, data_type)
        inserted = 0
        updated = 0
        unchanged = 0
        for record in records:
            path = self._record_path(record_dir, record, dedup_fields)
            if not path.exists():
                inserted += 1
                continue
            existing = self.read_json(path)
            if record_values_equal(self._business_content(existing), self._business_content(record)):
                unchanged += 1
            else:
                updated += 1
        self.last_save_stats = {
            "inserted": inserted,
            "updated": updated,
            "unchanged": unchanged,
            "deleted": 0,
        }
        paths: list[str] = []
        for record in records:
            path = await self.save_record(template_name, data_type, dedup_fields, record)
            paths.append(path)
        logger.info("Saved %d records for %s/%s", len(paths), template_name, data_type)
        return paths

    async def exists(self, template_name: str, data_type: str, record_id: str) -> bool:
        record_dir = self._get_record_dir(template_name, data_type)
        return (record_dir / f"{record_id}.json").exists()

    async def update_file_status(
        self,
        template_name: str,
        data_type: str,
        record_id: str,
        file_url: str,
        download_status: str,
    ) -> None:
        record_dir = self._get_record_dir(template_name, data_type)
        meta_path = record_dir / f"{record_id}.json"
        if not meta_path.exists():
            return
        data = self.read_json(meta_path)
        if "_meta" not in data:
            data["_meta"] = {}
        data["_meta"]["file_url"] = file_url
        data["_meta"]["download_status"] = download_status
        self.write_json(meta_path, data)

    async def update_sync_status(
        self,
        template_name: str,
        data_type: str,
        record_id: str,
        sync_status: str,
    ) -> None:
        record_dir = self._get_record_dir(template_name, data_type)
        meta_path = record_dir / f"{record_id}.json"
        if not meta_path.exists():
            return
        data = self.read_json(meta_path)
        if "_meta" not in data:
            data["_meta"] = {}
        data["_meta"]["sync_status"] = sync_status
        self.write_json(meta_path, data)

    async def update_record_fields(
        self,
        template_name: str,
        data_type: str,
        record_id: str,
        updates: dict[str, Any],
    ) -> None:
        record_dir = self._get_record_dir(template_name, data_type)
        meta_path = record_dir / f"{record_id}.json"
        if not meta_path.exists():
            return
        data = self.read_json(meta_path)
        for key, value in updates.items():
            data[key] = value
        self.write_json(meta_path, data)

    async def get_unsynced_records(
        self,
        template_name: str,
        data_type: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        record_dir = self._get_record_dir(template_name, data_type)
        results: list[dict[str, Any]] = []
        for json_file in record_dir.glob("*.json"):
            data = json.loads(json_file.read_text(encoding="utf-8"))
            meta = data.get("_meta", {})
            if meta.get("sync_status") != "synced":
                results.append(data)
                if len(results) >= limit:
                    break
        return results

    async def get_ready_to_sync(
        self,
        template_name: str,
        data_type: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        record_dir = self._get_record_dir(template_name, data_type)
        results: list[dict[str, Any]] = []
        for json_file in record_dir.glob("*.json"):
            data = json.loads(json_file.read_text(encoding="utf-8"))
            meta = data.get("_meta", {})
            sync_status = meta.get("sync_status")
            download_status = meta.get("download_status", "")
            if sync_status != "synced" and download_status in (
                "downloaded", "no_assets", "failed",
            ):
                results.append(data)
                if len(results) >= limit:
                    break
        return results

    async def close(self) -> None:
        pass
