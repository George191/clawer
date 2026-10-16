"""Contract for persisted crawler records and their lifecycle state."""

from __future__ import annotations

import abc
from typing import Any


class RecordStore(abc.ABC):
    @abc.abstractmethod
    async def save_record(
        self,
        template_name: str,
        data_type: str,
        dedup_fields: list[str],
        record: dict[str, Any],
    ) -> str:
        ...

    @abc.abstractmethod
    async def save_records(
        self,
        template_name: str,
        data_type: str,
        dedup_fields: list[str],
        records: list[dict[str, Any]],
    ) -> list[str]:
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
