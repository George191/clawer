"""水位线存储 — 跟踪 RDS→ES 增量同步进度。

每个 (syncer_name, source_table) 维护一条水位线记录，
记录上次同步的 (watermark, record_id, data_source, data_type) 稳定游标。

设计原则:
- 水位线存储在 PostgreSQL（与源数据同库），事务一致性
- 幂等更新：重复同步同一批次不会回退水位线
- 启动时自动建表（幂等）
"""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from typing import Any

from app.logger import get_logger
from app.storage.postgres_client import PostgresClient, get_pg_client

logger = get_logger(__name__)

TABLE_NAME = "public.syncer_watermarks"
_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    syncer_name TEXT NOT NULL, source_table TEXT NOT NULL, target_index TEXT NOT NULL,
    last_watermark TIMESTAMPTZ, last_sync_at TIMESTAMPTZ,
    last_record_id TEXT, last_data_source TEXT, last_data_type TEXT,
    last_sync_count BIGINT NOT NULL DEFAULT 0, last_error TEXT,
    total_synced BIGINT NOT NULL DEFAULT 0, total_errors BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (syncer_name, source_table)
);
"""
_CURSOR_DDL = f"""
ALTER TABLE {TABLE_NAME}
    ADD COLUMN IF NOT EXISTS last_record_id TEXT,
    ADD COLUMN IF NOT EXISTS last_data_source TEXT,
    ADD COLUMN IF NOT EXISTS last_data_type TEXT;
"""


@dataclass(frozen=True)
class BackfillCursor:
    """Last source row in PostgreSQL's ordered timestamp/business-identity tuple."""

    watermark: datetime
    identity: tuple[str, str, str] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.watermark, datetime):
            raise ValueError("ES backfill watermark must be a timestamp")
        if self.identity is not None and (len(self.identity) != 3 or
                                         any(not isinstance(value, str) or not value for value in self.identity)):
            raise ValueError("ES backfill requires the complete business identity")


class WatermarkStore:
    """水位线存储 — 基于 PostgreSQL。

    用法:
        store = WatermarkStore()
        await store.ensure_table()
        watermark = await store.get("es_syncer", "ts_rds.rds_patent")
        await store.update("es_syncer", "ts_rds.rds_patent", new_watermark, count=100)
    """

    def __init__(self, pg: PostgresClient | None = None) -> None:
        self._pg = pg
        self._ready = False

    def _get_pg(self) -> PostgresClient:
        if self._pg is None:
            self._pg = get_pg_client()
        return self._pg

    async def ensure_table(self) -> None:
        """确保水位线表存在（幂等）。"""
        if self._ready:
            return

        pg = self._get_pg()
        await pg.connect()

        row = await pg.fetch_one(
            "SELECT to_regclass(:table_name) AS reg",
            {"table_name": TABLE_NAME},
        )
        if row and row.get("reg"):
            await pg.init_schema([_CURSOR_DDL])
        else:
            await pg.init_schema([_DDL])
        logger.info("syncer_watermarks table initialized")

        self._ready = True

    async def get_cursor(
        self, syncer_name: str, source_table: str, *, target_index: str | None = None,
    ) -> BackfillCursor | None:
        """Legacy timestamp-only checkpoints replay their boundary before advancing."""
        await self.ensure_table()
        row = await self._get_pg().fetch_one(
            f"""SELECT last_watermark, last_record_id, last_data_source, last_data_type, target_index
                FROM {TABLE_NAME}
                WHERE syncer_name = :syncer_name AND source_table = :source_table""",
            {"syncer_name": syncer_name, "source_table": source_table},
        )
        if not row or row.get("last_watermark") is None:
            return None
        if target_index is not None and row["target_index"] != target_index:
            raise ValueError("A different ES index requires a separate syncer_name for a complete backfill")
        identity = tuple(row.get(key) for key in ("last_record_id", "last_data_source", "last_data_type"))
        return BackfillCursor(row["last_watermark"], identity if all(value is not None for value in identity) else None)

    async def get(
        self,
        syncer_name: str,
        source_table: str,
    ) -> datetime | None:
        """获取水位线值。

        Returns:
            上次同步的 watermark 值，无记录时返回 None（首次同步）
        """
        await self.ensure_table()
        row = await self._get_pg().fetch_one(
            f"""
            SELECT last_watermark
            FROM {TABLE_NAME}
            WHERE syncer_name = :syncer_name AND source_table = :source_table
            """,
            {"syncer_name": syncer_name, "source_table": source_table},
        )
        if row and row.get("last_watermark"):
            return row["last_watermark"]
        return None

    async def update(
        self,
        syncer_name: str,
        source_table: str,
        target_index: str,
        watermark: datetime,
        sync_count: int = 0,
        error: str | None = None,
        cursor_identity: tuple[str, str, str] | None = None,
    ) -> None:
        """更新水位线（upsert）。

        按时间与三元业务键比较，重复或较旧批次不回退游标。
        """
        await self.ensure_table()

        is_error = error is not None
        advance = f"""({TABLE_NAME}.last_watermark IS NULL OR
            ROW({TABLE_NAME}.last_watermark, COALESCE({TABLE_NAME}.last_record_id, ''),
                COALESCE({TABLE_NAME}.last_data_source, ''), COALESCE({TABLE_NAME}.last_data_type, '')) <
            ROW(EXCLUDED.last_watermark, COALESCE(EXCLUDED.last_record_id, ''),
                COALESCE(EXCLUDED.last_data_source, ''), COALESCE(EXCLUDED.last_data_type, '')))"""
        await self._get_pg().execute(
            f"""
            INSERT INTO {TABLE_NAME} (
                syncer_name, source_table, target_index,
                last_watermark, last_record_id, last_data_source, last_data_type,
                last_sync_at, last_sync_count, last_error,
                total_synced, total_errors
            ) VALUES (
                :syncer_name, :source_table, :target_index,
                :watermark, :record_id, :data_source, :data_type, NOW(), :sync_count, :error,
                :sync_count, :error_count
            )
            ON CONFLICT (syncer_name, source_table) DO UPDATE SET
                target_index = EXCLUDED.target_index,
                last_watermark = CASE WHEN {advance} THEN EXCLUDED.last_watermark ELSE {TABLE_NAME}.last_watermark END,
                last_record_id = CASE WHEN {advance} THEN EXCLUDED.last_record_id ELSE {TABLE_NAME}.last_record_id END,
                last_data_source = CASE WHEN {advance} THEN EXCLUDED.last_data_source ELSE {TABLE_NAME}.last_data_source END,
                last_data_type = CASE WHEN {advance} THEN EXCLUDED.last_data_type ELSE {TABLE_NAME}.last_data_type END,
                last_sync_at = EXCLUDED.last_sync_at,
                last_sync_count = EXCLUDED.last_sync_count,
                last_error = EXCLUDED.last_error,
                total_synced = {TABLE_NAME}.total_synced + EXCLUDED.total_synced,
                total_errors = {TABLE_NAME}.total_errors + EXCLUDED.total_errors,
                updated_at = NOW()
            """,
            {
                "syncer_name": syncer_name,
                "source_table": source_table,
                "target_index": target_index,
                "watermark": watermark,
                "record_id": cursor_identity[0] if cursor_identity else None,
                "data_source": cursor_identity[1] if cursor_identity else None,
                "data_type": cursor_identity[2] if cursor_identity else None,
                "sync_count": sync_count,
                "error": error,
                "error_count": 1 if is_error else 0,
            },
        )

    async def get_status(self, syncer_name: str) -> list[dict[str, Any]]:
        """获取同步器所有表的状态（用于监控）。"""
        await self.ensure_table()
        return await self._get_pg().fetch_all(
            f"""
            SELECT syncer_name, source_table, target_index,
                   last_watermark, last_record_id, last_data_source, last_data_type,
                   last_sync_at, last_sync_count, last_error,
                   total_synced, total_errors,
                   created_at, updated_at
            FROM {TABLE_NAME}
            WHERE syncer_name = :syncer_name
            ORDER BY source_table
            """,
            {"syncer_name": syncer_name},
        )


def get_watermark_store() -> WatermarkStore:
    """获取全局 WatermarkStore 单例。"""
    global _watermark_store
    if _watermark_store is None:
        _watermark_store = WatermarkStore()
    return _watermark_store


_watermark_store: WatermarkStore | None = None
