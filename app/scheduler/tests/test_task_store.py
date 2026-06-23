"""任务持久化存储 — 单元测试。

测试覆盖:
    - TaskStore 记录创建/开始/成功/失败
    - 查询接口（历史、最新、统计）
    - 状态流转正确性
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
    """创建模拟的 MongoDB collection。"""
    coll = MagicMock()
    coll.update_one = AsyncMock()
    coll.find_one = AsyncMock()
    coll.find = MagicMock()
    coll.create_index = AsyncMock()
    coll.aggregate = MagicMock()
    return coll


def make_mock_store(coll: MagicMock) -> TaskStore:
    """创建使用 mock collection 的 TaskStore。"""
    store = TaskStore()
    store._collection = coll
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

        coll.find_one.return_value = {
            "started_at": datetime(2026, 6, 22, 10, 0, tzinfo=timezone.utc),
            "finished_at": datetime(2026, 6, 22, 10, 5, tzinfo=timezone.utc),
        }

        await store.record_success("task-1", {"records": 100})

        assert coll.update_one.call_count >= 1

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

        mock_cursor = MagicMock()
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=[{"_id": "task-1", "status": "success"}])
        coll.find.return_value = mock_cursor

        result = await store.get_latest("test_task")

        assert result is not None
        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_get_history(self) -> None:
        coll = make_mock_collection()
        store = make_mock_store(coll)

        mock_cursor = MagicMock()
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=[
            {"_id": "task-1", "status": "success"},
            {"_id": "task-2", "status": "failed"},
        ])
        coll.find.return_value = mock_cursor

        results = await store.get_history(task_name="test_task", limit=10)

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_get_stats(self) -> None:
        coll = make_mock_collection()
        store = make_mock_store(coll)

        mock_cursor = MagicMock()
        mock_cursor.to_list = AsyncMock(return_value=[
            {"_id": "success", "count": 8},
            {"_id": "failed", "count": 2},
        ])
        coll.aggregate.return_value = mock_cursor

        stats = await store.get_stats("test_task")

        assert stats["success"] == 8
        assert stats["failed"] == 2
        assert stats["total"] == 10
