"""水位线存储 — 跟踪 RDS→ES 增量同步进度。

每个 (syncer_name, source_table) 维护一条水位线记录，
记录上次同步的 watermark 值（RDS 表 updated_at 最大值）。

设计原则:
- 水位线存储在 PostgreSQL（与源数据同库），事务一致性
- 幂等更新：重复同步同一批次不会回退水位线
- 启动时自动建表（幂等）
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.storage.postgres_client import PostgresClient, get_pg_client

logger = logging.getLogger(__name__)

TABLE_NAME = "public.syncer_watermarks"
_DDL_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "scripts" / "init_syncer_watermarks.sql"
)
_READY = False


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

    def _get_pg(self) -> PostgresClient:
        if self._pg is None:
            self._pg = get_pg_client()
        return self._pg

    async def ensure_table(self) -> None:
        """确保水位线表存在（幂等）。"""
        global _READY
        if _READY:
            return

        pg = self._get_pg()
        await pg.connect()

        row = await pg.fetch_one(
            "SELECT to_regclass(:table_name) AS reg",
            {"table_name": TABLE_NAME},
        )
        if row and row.get("reg"):
            _READY = True
            return

        if _DDL_PATH.exists():
            ddl = _DDL_PATH.read_text(encoding="utf-8")
            await pg.init_schema([ddl])
            logger.info("syncer_watermarks table initialized")
        else:
            raise FileNotFoundError(f"DDL script not found: {_DDL_PATH}")

        _READY = True

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
    ) -> None:
        """更新水位线（upsert）。

        水位线只前进不回退：新值 > 旧值时才更新。
        """
        await self.ensure_table()

        is_error = error is not None
        await self._get_pg().execute(
            f"""
            INSERT INTO {TABLE_NAME} (
                syncer_name, source_table, target_index,
                last_watermark, last_sync_at, last_sync_count, last_error,
                total_synced, total_errors
            ) VALUES (
                :syncer_name, :source_table, :target_index,
                :watermark, NOW(), :sync_count, :error,
                :sync_count, :error_count
            )
            ON CONFLICT (syncer_name, source_table) DO UPDATE SET
                target_index = EXCLUDED.target_index,
                last_watermark = GREATEST(
                    {TABLE_NAME}.last_watermark, EXCLUDED.last_watermark
                ),
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
                   last_watermark, last_sync_at, last_sync_count, last_error,
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
