"""文件存储后端 — 将采集结果以 JSON 文件形式持久化到本地磁盘。

提供 StorageBackend 抽象基类和 FileStorage 本地文件实现，
支持状态管理（download_status / sync_status）和变更历史追踪。
"""

from __future__ import annotations

import abc
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from app.config.settings import settings

logger = logging.getLogger(__name__)


class StorageBackend(abc.ABC):
    @abc.abstractmethod
    async def save_record(self, template_name: str, data_type: str, record: dict[str, Any]) -> str:
        ...

    @abc.abstractmethod
    async def save_records(self, template_name: str, data_type: str, records: list[dict[str, Any]]) -> list[str]:
        ...

    @abc.abstractmethod
    async def exists(self, template_name: str, data_type: str, record_id: str) -> bool:
        ...

    @abc.abstractmethod
    async def update_file_status(
        self,
        template_name: str,
        data_type: str,
        record_id: str,
        file_url: str,
        download_status: str,
    ) -> None:
        ...

    @abc.abstractmethod
    async def update_sync_status(
        self,
        template_name: str,
        data_type: str,
        record_id: str,
        sync_status: str,
    ) -> None:
        ...

    @abc.abstractmethod
    async def update_record_fields(
        self,
        template_name: str,
        data_type: str,
        record_id: str,
        updates: dict[str, Any],
    ) -> None:
        ...

    @abc.abstractmethod
    async def get_unsynced_records(
        self,
        template_name: str,
        data_type: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        ...

    @abc.abstractmethod
    async def get_ready_to_sync(
        self,
        template_name: str,
        data_type: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        ...


class FileStorage(StorageBackend):
    def __init__(self, base_dir: str | None = None) -> None:
        self._base_dir = Path(base_dir or settings.output_dir)

    def _get_record_dir(self, template_name: str, data_type: str) -> Path:
        record_dir = self._base_dir / template_name / data_type
        record_dir.mkdir(parents=True, exist_ok=True)
        return record_dir

    def _resolve_record_id(self, record: dict[str, Any]) -> str:
        for key in ("id", "uid", "contract_no", "patent_id", "title"):
            if key in record and record[key]:
                value = str(record[key]).strip()
                safe_value = "".join(c if c.isalnum() or c in "-_" else "_" for c in value)
                return safe_value[:200]
        content = json.dumps(record, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(content.encode()).hexdigest()

    async def save_record(self, template_name: str, data_type: str, record: dict[str, Any]) -> str:
        record_dir = self._get_record_dir(template_name, data_type)
        record_id = self._resolve_record_id(record)
        file_path = record_dir / f"{record_id}.json"

        search_params = record.pop("_meta_search_params", None) or {}

        record_with_meta = {
            **record,
            "_meta": {
                "template": template_name,
                "data_type": data_type,
                "record_id": record_id,
                "download_status": "pending",
                "sync_status": "pending",
                "search_params": search_params,
            },
        }

        file_path.write_text(
            json.dumps(record_with_meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.debug("Saved record: %s", file_path)
        return str(file_path)

    async def save_records(
        self, template_name: str, data_type: str, records: list[dict[str, Any]]
    ) -> list[str]:
        paths: list[str] = []
        for record in records:
            path = await self.save_record(template_name, data_type, record)
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
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        if "_meta" not in data:
            data["_meta"] = {}
        data["_meta"]["file_url"] = file_url
        data["_meta"]["download_status"] = download_status
        meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

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
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        if "_meta" not in data:
            data["_meta"] = {}
        data["_meta"]["sync_status"] = sync_status
        meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

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
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        for key, value in updates.items():
            data[key] = value
        meta_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

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
            if sync_status != "synced" and download_status in ("downloaded", "no_assets"):
                results.append(data)
                if len(results) >= limit:
                    break
        return results

    async def close(self) -> None:
        pass