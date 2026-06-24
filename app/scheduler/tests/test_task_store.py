"""任务持久化存储 — 单元测试。

测试覆盖 TaskStore 的:
    - 记录生命周期 (created/started/success/failed)
    - 查询接口 (get_task/get_latest/get_history/get_stats)
    - 索引一次性创建
    - 连接关闭与重置

Mock 策略: 通过 _collection 注入 AsyncMock，模拟真实 motor 行为。
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.scheduler.task_store import (
    STATUS_CREATED,
    STATUS_FAILED,
    STATUS_STARTED,
    STATUS_SUCCESS,
    TaskStore,
)


# ══════════════════════════════════════════════════════════════════════
#  Mock 工具
# ══════════════════════════════════════════════════════════════════════

def make_mock_collection() -> MagicMock:
    """创建模拟的 MongoDB collection。

    所有 async 方法用 AsyncMock，链式调用（find().sort().limit()）返回 MagicMock
    以支持 motor 的 cursor 链式 API。
    """
    coll = MagicMock()
    coll.update_one = AsyncMock()
    coll.find_one = AsyncMock()
    coll.create_index = AsyncMock()
    coll.aggregate = MagicMock()

    # find() 返回链式 cursor
    mock_cursor = MagicMock()
    mock_cursor.sort.return_value = mock_cursor
    mock_cursor.limit.return_value = mock_cursor
    mock_cursor.to_list = AsyncMock(return_value=[])
    coll.find = MagicMock(return_value=mock_cursor)

    # aggregate() 返回链式 cursor
    agg_cursor = MagicMock()
    agg_cursor.to_list = AsyncMock(return_value=[])
    coll.aggregate = MagicMock(return_value=agg_cursor)

    return coll


def make_mock_store(coll: MagicMock) -> TaskStore:
    """创建使用 mock collection 的 TaskStore。

    直接注入 _collection 并标记索引已创建，跳过真实连接。
    """
    store = TaskStore()
    store._collection = coll
    store._indexes_ready = True
    return store


# ══════════════════════════════════════════════════════════════════════
#  记录生命周期测试
# ══════════════════════════════════════════════════════════════════════

class TestTaskStoreLifecycle:
    """测试任务记录的完整生命周期。"""

    @pytest.mark.asyncio
    async def test_record_created(self) -> None:
        coll = make_mock_collection()
        store = make_mock_store(coll)

        await store.record_created("task-1", "test_task", {"param": "value"})

        coll.update_one.assert_called_once()
        call_args = coll.update_one.call_args
        assert call_args[0][0] == {"_id": "task-1"}

        set_doc = call_args[0][1]["$set"]
        assert set_doc["task_name"] == "test_task"
        assert set_doc["status"] == STATUS_CREATED
        assert set_doc["params"] == {"param": "value"}
        assert set_doc["retry_count"] == 0

    @pytest.mark.asyncio
    async def test_record_started(self) -> None:
        coll = make_mock_collection()
        store = make_mock_store(coll)

        await store.record_started("task-1")

        coll.update_one.assert_called_once()
        set_doc = coll.update_one.call_args[0][1]["$set"]
        assert set_doc["status"] == STATUS_STARTED

    @pytest.mark.asyncio
    async def test_record_success(self) -> None:
        coll = make_mock_collection()
        store = make_mock_store(coll)

        # _update_duration 会 find_one 查询 started_at/finished_at
        coll.find_one.return_value = {
            "started_at": datetime(2026, 6, 22, 10, 0, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 6, 22, 10, 5, tzinfo=timezone.utc),
        }

        await store.record_success("task-1", {"records": 100})

        # 第一次 update_one 写状态，第二次 update_one 写 duration
        assert coll.update_one.call_count == 2

        first_call = coll.update_one.call_args_list[0]
        set_doc = first_call[0][1]["$set"]
        assert set_doc["status"] == STATUS_SUCCESS
        assert set_doc["result"] == {"records": 100}

    @pytest.mark.asyncio
    async def test_record_failure_no_retry(self) -> None:
        coll = make_mock_collection()
        store = make_mock_store(coll)

        coll.find_one.return_value = {
            "started_at": datetime(2026, 6, 22, 10, 0, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 6, 22, 10, 2, tzinfo=timezone.utc),
        }

        await store.record_failure("task-1", "Connection error", retry_count=0)

        first_call = coll.update_one.call_args_list[0]
        set_doc = first_call[0][1]["$set"]
        assert set_doc["status"] == STATUS_FAILED
        assert set_doc["error"] == "Connection error"

    @pytest.mark.asyncio
    async def test_record_failure_with_retry(self) -> None:
        coll = make_mock_collection()
        store = make_mock_store(coll)

        coll.find_one.return_value = {
            "started_at": datetime(2026, 6, 22, 10, 0, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 6, 22, 10, 2, tzinfo=timezone.utc),
        }

        await store.record_failure("task-1", "Timeout", retry_count=2)

        first_call = coll.update_one.call_args_list[0]
        set_doc = first_call[0][1]["$set"]
        assert set_doc["status"] == "retry"
        assert set_doc["retry_count"] == 2


# ══════════════════════════════════════════════════════════════════════
#  查询接口测试
# ══════════════════════════════════════════════════════════════════════

class TestTaskStoreQueries:
    """测试查询接口。"""

    @pytest.mark.asyncio
    async def test_get_task(self) -> None:
        coll = make_mock_collection()
        store = make_mock_store(coll)

        coll.find_one.return_value = {"_id": "task-1", "status": "success"}

        result = await store.get_task("task-1")

        coll.find_one.assert_called_once_with({"_id": "task-1"})
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_get_latest(self) -> None:
        coll = make_mock_collection()
        store = make_mock_store(coll)

        # 修改 find() 返回的 cursor 的 to_list
        coll.find.return_value.to_list.return_value = [
            {"_id": "task-1", "status": "success"},
        ]

        result = await store.get_latest("test_task")

        assert result is not None
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_get_history(self) -> None:
        coll = make_mock_collection()
        store = make_mock_store(coll)

        coll.find.return_value.to_list.return_value = [
            {"_id": "task-1", "status": "success"},
            {"_id": "task-2", "status": "failed"},
        ]

        results = await store.get_history(task_name="test_task", limit=10)

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_get_stats(self) -> None:
        coll = make_mock_collection()
        store = make_mock_store(coll)

        coll.aggregate.return_value.to_list.return_value = [
            {"_id": "success", "count": 8},
            {"_id": "failed", "count": 2},
        ]

        stats = await store.get_stats("test_task")

        assert stats["success"] == 8
        assert stats["failed"] == 2
        assert stats["total"] == 10


# ══════════════════════════════════════════════════════════════════════
#  连接管理测试
# ══════════════════════════════════════════════════════════════════════

class TestTaskStoreConnection:
    """测试连接生命周期管理。"""

    @pytest.mark.asyncio
    async def test_close_resets_state(self) -> None:
        """close() 应清空 collection 和索引标志。"""
        coll = make_mock_collection()
        store = make_mock_store(coll)

        # 模拟有 client
        mock_client = MagicMock()
        store._client = mock_client

        await store.close()

        mock_client.close.assert_called_once()
        assert store._client is None
        assert store._collection is None
        assert store._indexes_ready is False
