"""WatermarkStore — 单元测试。

测试水位线的读取、更新（GREATEST 语义）和状态查询。
使用 mock PostgresClient，不依赖真实数据库。
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.syncer.es.watermark import WatermarkStore


@pytest.fixture
def mock_pg() -> MagicMock:
    pg = MagicMock()
    pg.connect = AsyncMock()
    pg.fetch_all = AsyncMock(return_value=[])
    pg.fetch_one = AsyncMock(return_value=None)
    pg.execute = AsyncMock()
    pg.init_schema = AsyncMock()
    return pg


@pytest.fixture
def store(mock_pg: MagicMock) -> WatermarkStore:
    import app.syncer.es.watermark as mod
    mod._READY = False
    return WatermarkStore(pg=mock_pg)


class TestWatermarkStore:
    """测试 WatermarkStore。"""

    @pytest.mark.asyncio
    async def test_ensure_table_skips_when_ready(self, mock_pg: MagicMock) -> None:
        """_READY=True 时应跳过。"""
        import app.syncer.es.watermark as mod
        mod._READY = True
        store = WatermarkStore(pg=mock_pg)
        await store.ensure_table()
        mock_pg.connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_table_creates_when_missing(self, store: WatermarkStore, mock_pg: MagicMock) -> None:
        """表不存在时应执行 DDL。"""
        mock_pg.fetch_one.return_value = {"reg": None}
        with __import__("unittest.mock").mock.patch("app.syncer.es.watermark._DDL_PATH") as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = "CREATE TABLE..."
            await store.ensure_table()
            mock_pg.init_schema.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_table_skips_when_exists(self, store: WatermarkStore, mock_pg: MagicMock) -> None:
        """表已存在时应跳过 DDL。"""
        mock_pg.fetch_one.return_value = {"reg": "syncer_watermarks"}
        await store.ensure_table()
        mock_pg.init_schema.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_returns_none_when_no_record(self, store: WatermarkStore, mock_pg: MagicMock) -> None:
        """无记录时应返回 None。"""
        mock_pg.fetch_one.return_value = {"reg": "syncer_watermarks"}
        # 第二次 fetch_one 返回 None（水位线查询）
        mock_pg.fetch_one.side_effect = [{"reg": "syncer_watermarks"}, None]
        result = await store.get("es_syncer", "ts_rds.rds_patent")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_returns_watermark(self, store: WatermarkStore, mock_pg: MagicMock) -> None:
        """有记录时应返回水位线值。"""
        watermark = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mock_pg.fetch_one.side_effect = [
            {"reg": "syncer_watermarks"},  # ensure_table
            {"last_watermark": watermark},  # 查询
        ]
        result = await store.get("es_syncer", "ts_rds.rds_patent")
        assert result == watermark

    @pytest.mark.asyncio
    async def test_update_inserts_new_record(self, store: WatermarkStore, mock_pg: MagicMock) -> None:
        """更新时应执行 upsert SQL。"""
        mock_pg.fetch_one.return_value = {"reg": "syncer_watermarks"}
        watermark = datetime(2026, 1, 1, tzinfo=timezone.utc)

        await store.update(
            syncer_name="es_syncer",
            source_table="ts_rds.rds_patent",
            target_index="spider_patent",
            watermark=watermark,
            sync_count=100,
        )

        mock_pg.execute.assert_called_once()
        call_args = mock_pg.execute.call_args
        params = call_args[0][1]
        assert params["syncer_name"] == "es_syncer"
        assert params["source_table"] == "ts_rds.rds_patent"
        assert params["watermark"] == watermark
        assert params["sync_count"] == 100
        assert params["error"] is None
        assert params["error_count"] == 0

    @pytest.mark.asyncio
    async def test_update_with_error(self, store: WatermarkStore, mock_pg: MagicMock) -> None:
        """错误更新时应记录 error 信息。"""
        mock_pg.fetch_one.return_value = {"reg": "syncer_watermarks"}
        watermark = datetime(2026, 1, 1, tzinfo=timezone.utc)

        await store.update(
            syncer_name="es_syncer",
            source_table="ts_rds.rds_patent",
            target_index="spider_patent",
            watermark=watermark,
            sync_count=0,
            error="ES connection refused",
        )

        params = mock_pg.execute.call_args[0][1]
        assert params["error"] == "ES connection refused"
        assert params["error_count"] == 1

    @pytest.mark.asyncio
    async def test_get_status(self, store: WatermarkStore, mock_pg: MagicMock) -> None:
        """get_status 应返回所有表的状态。"""
        mock_pg.fetch_one.return_value = {"reg": "syncer_watermarks"}
        mock_pg.fetch_all.return_value = [
            {"source_table": "ts_rds.rds_patent", "total_synced": 1000},
            {"source_table": "ts_rds.rds_news", "total_synced": 500},
        ]

        status = await store.get_status("es_syncer")
        assert len(status) == 2
        assert status[0]["source_table"] == "ts_rds.rds_patent"
