"""TaskRepository — 单元测试。

测试 TaskConfig 数据模型和 TaskRepository 的 CRUD 逻辑。
使用 mock PostgresClient，不依赖真实数据库。
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scheduler.task_repository import TaskConfig, TaskRepository


# ══════════════════════════════════════════════════════════════════════
#  TaskConfig 数据模型测试
# ══════════════════════════════════════════════════════════════════════

class TestTaskConfig:
    """测试 TaskConfig 数据模型。"""

    def test_default_values(self) -> None:
        """默认值应为 crontab 调度，所有 cron 字段为 *。"""
        cfg = TaskConfig(task_name="test", task_path="app.test.task")
        assert cfg.schedule_type == "crontab"
        assert cfg.cron_minute == "*"
        assert cfg.cron_hour == "*"
        assert cfg.enabled is True
        assert cfg.args == []
        assert cfg.kwargs == {}
        assert cfg.options == {}

    def test_to_beat_entry_crontab(self) -> None:
        """crontab 类型应生成 crontab 调度。"""
        from celery.schedules import crontab

        cfg = TaskConfig(
            task_name="test",
            task_path="app.test.task",
            cron_minute="0",
            cron_hour="6",
            options={"queue": "patent"},
        )
        entry = cfg.to_beat_entry()
        assert entry["task"] == "app.test.task"
        assert isinstance(entry["schedule"], crontab)
        assert entry["args"] == ()
        assert entry["kwargs"] == {}
        assert entry["options"] == {"queue": "patent"}

    def test_to_beat_entry_interval(self) -> None:
        """interval 类型应生成 schedule 调度。"""
        from celery.schedules import schedule as interval_schedule

        cfg = TaskConfig(
            task_name="test",
            task_path="app.test.task",
            schedule_type="interval",
            interval_seconds=3600,
        )
        entry = cfg.to_beat_entry()
        assert isinstance(entry["schedule"], interval_schedule)
        assert entry["schedule"].run_every.total_seconds() == 3600

    def test_to_beat_entry_with_args_kwargs(self) -> None:
        """args/kwargs/options 应正确传递。"""
        cfg = TaskConfig(
            task_name="test",
            task_path="app.test.task",
            args=["2026-01-01"],
            kwargs={"days_back": 7},
            options={"queue": "patent", "expires": 3600},
        )
        entry = cfg.to_beat_entry()
        assert entry["args"] == ("2026-01-01",)
        assert entry["kwargs"] == {"days_back": 7}
        assert entry["options"]["expires"] == 3600


# ══════════════════════════════════════════════════════════════════════
#  TaskRepository 测试（使用 mock PostgresClient）
# ══════════════════════════════════════════════════════════════════════

@pytest.fixture
def mock_pg() -> MagicMock:
    """Mock PostgresClient。"""
    pg = MagicMock()
    pg.connect = AsyncMock()
    pg.fetch_all = AsyncMock(return_value=[])
    pg.fetch_one = AsyncMock(return_value=None)
    pg.execute = AsyncMock()
    pg.init_schema = AsyncMock()
    return pg


@pytest.fixture
def repo(mock_pg: MagicMock) -> TaskRepository:
    """带 mock pg 的 TaskRepository。"""
    # 重置 _READY 标志，确保 ensure_table 被调用
    import app.scheduler.task_repository as mod
    mod._READY = False
    return TaskRepository(pg=mock_pg)


def _make_row(**overrides: Any) -> dict[str, Any]:
    """构造数据库行。"""
    row = {
        "id": 1,
        "task_name": "test_task",
        "task_path": "app.test.task",
        "description": "test description",
        "schedule_type": "crontab",
        "cron_minute": "0",
        "cron_hour": "6",
        "cron_day_of_week": "*",
        "cron_day_of_month": "*",
        "cron_month_of_year": "*",
        "interval_seconds": None,
        "args": [],
        "kwargs": {},
        "options": {"queue": "test"},
        "enabled": True,
        "updated_by": "user",
        "created_at": "2026-01-01 00:00:00+00:00",
        "updated_at": "2026-01-01 00:00:00+00:00",
    }
    row.update(overrides)
    return row


class TestTaskRepository:
    """测试 TaskRepository CRUD 逻辑。"""

    @pytest.mark.asyncio
    async def test_ensure_table_skips_when_ready(self, mock_pg: MagicMock) -> None:
        """_READY=True 时应跳过初始化。"""
        import app.scheduler.task_repository as mod
        mod._READY = True
        repo = TaskRepository(pg=mock_pg)
        await repo.ensure_table()
        mock_pg.connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_table_creates_when_table_missing(self, repo: TaskRepository, mock_pg: MagicMock) -> None:
        """表不存在时应执行 DDL。"""
        # to_regclass 返回 None（表不存在）
        mock_pg.fetch_one.return_value = {"reg": None}
        with patch("app.scheduler.task_repository._DDL_PATH") as mock_path:
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = "CREATE TABLE..."
            await repo.ensure_table()
            mock_pg.init_schema.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_table_skips_when_table_exists(self, repo: TaskRepository, mock_pg: MagicMock) -> None:
        """表已存在时应跳过 DDL。"""
        mock_pg.fetch_one.return_value = {"reg": "scheduler_tasks"}
        await repo.ensure_table()
        mock_pg.init_schema.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_all(self, repo: TaskRepository, mock_pg: MagicMock) -> None:
        """list_all 应返回所有任务配置。"""
        mock_pg.fetch_one.return_value = {"reg": "scheduler_tasks"}
        mock_pg.fetch_all.return_value = [
            _make_row(task_name="task1"),
            _make_row(task_name="task2", id=2),
        ]
        configs = await repo.list_all()
        assert len(configs) == 2
        assert configs[0].task_name == "task1"
        assert configs[1].task_name == "task2"

    @pytest.mark.asyncio
    async def test_list_enabled(self, repo: TaskRepository, mock_pg: MagicMock) -> None:
        """list_enabled 应只返回启用的任务。"""
        mock_pg.fetch_one.return_value = {"reg": "scheduler_tasks"}
        mock_pg.fetch_all.return_value = [_make_row(enabled=True)]
        configs = await repo.list_enabled()
        assert len(configs) == 1
        assert configs[0].enabled is True

    @pytest.mark.asyncio
    async def test_get_by_name_found(self, repo: TaskRepository, mock_pg: MagicMock) -> None:
        """get_by_name 找到任务时应返回配置。"""
        mock_pg.fetch_one.side_effect = [
            {"reg": "scheduler_tasks"},  # ensure_table 检查
            _make_row(task_name="test_task"),  # 查询
        ]
        cfg = await repo.get_by_name("test_task")
        assert cfg is not None
        assert cfg.task_name == "test_task"
        assert cfg.task_path == "app.test.task"

    @pytest.mark.asyncio
    async def test_get_by_name_not_found(self, repo: TaskRepository, mock_pg: MagicMock) -> None:
        """get_by_name 未找到时应返回 None。"""
        mock_pg.fetch_one.side_effect = [
            {"reg": "scheduler_tasks"},  # ensure_table
            None,  # 查询返回空
        ]
        cfg = await repo.get_by_name("non_existent")
        assert cfg is None

    @pytest.mark.asyncio
    async def test_create_duplicate_raises(self, repo: TaskRepository, mock_pg: MagicMock) -> None:
        """创建重复任务名应抛出 ValueError。"""
        mock_pg.fetch_one.side_effect = [
            {"reg": "scheduler_tasks"},  # ensure_table
            _make_row(),  # get_by_name 返回已存在
        ]
        config = TaskConfig(task_name="test_task", task_path="app.test.task")
        with pytest.raises(ValueError, match="任务名已存在"):
            await repo.create(config)

    @pytest.mark.asyncio
    async def test_create_success(self, repo: TaskRepository, mock_pg: MagicMock) -> None:
        """创建新任务应执行 INSERT 并返回结果。"""
        mock_pg.fetch_one.side_effect = [
            {"reg": "scheduler_tasks"},  # ensure_table
            None,  # get_by_name 检查不存在
            _make_row(task_name="new_task"),  # 创建后查询返回
        ]
        config = TaskConfig(task_name="new_task", task_path="app.test.task")
        result = await repo.create(config, updated_by="api")
        assert result.task_name == "new_task"
        mock_pg.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_no_fields_raises(self, repo: TaskRepository, mock_pg: MagicMock) -> None:
        """没有可更新字段时应抛出 ValueError。"""
        mock_pg.fetch_one.return_value = {"reg": "scheduler_tasks"}
        with pytest.raises(ValueError, match="没有可更新的字段"):
            await repo.update("test", {})

    @pytest.mark.asyncio
    async def test_update_success(self, repo: TaskRepository, mock_pg: MagicMock) -> None:
        """更新任务应执行 UPDATE 并返回结果。"""
        mock_pg.fetch_one.side_effect = [
            {"reg": "scheduler_tasks"},  # ensure_table
            _make_row(description="updated"),  # 更新后查询
        ]
        result = await repo.update("test_task", {"description": "updated"})
        assert result is not None
        assert result.description == "updated"
        mock_pg.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_update_json_fields(self, repo: TaskRepository, mock_pg: MagicMock) -> None:
        """更新 args/kwargs/options 应转为 JSON。"""
        mock_pg.fetch_one.side_effect = [
            {"reg": "scheduler_tasks"},
            _make_row(),
        ]
        await repo.update("test_task", {
            "args": ["a", "b"],
            "kwargs": {"k": "v"},
            "options": {"queue": "q"},
        })
        # 验证 execute 被调用，参数包含 JSON 字符串
        call_args = mock_pg.execute.call_args
        params = call_args[0][1]
        assert "args" in params
        assert "kwargs" in params
        assert "options" in params

    @pytest.mark.asyncio
    async def test_delete(self, repo: TaskRepository, mock_pg: MagicMock) -> None:
        """删除任务应执行 DELETE。"""
        mock_pg.fetch_one.return_value = {"reg": "scheduler_tasks"}
        result = await repo.delete("test_task")
        assert result is True
        mock_pg.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_toggle(self, repo: TaskRepository, mock_pg: MagicMock) -> None:
        """toggle 应调用 update 设置 enabled。"""
        mock_pg.fetch_one.side_effect = [
            {"reg": "scheduler_tasks"},
            _make_row(enabled=False),
        ]
        result = await repo.toggle("test_task", False)
        assert result is not None
        assert result.enabled is False


# ══════════════════════════════════════════════════════════════════════
#  _row_to_config 辅助函数测试
# ══════════════════════════════════════════════════════════════════════

class TestRowToConfig:
    """测试 _row_to_config 转换。"""

    def test_basic_conversion(self) -> None:
        row = _make_row()
        cfg = TaskRepository._row_to_config(row)
        assert cfg.id == 1
        assert cfg.task_name == "test_task"
        assert cfg.task_path == "app.test.task"
        assert cfg.schedule_type == "crontab"
        assert cfg.enabled is True

    def test_json_string_parsing(self) -> None:
        """JSON 字段为字符串时应正确解析。"""
        row = _make_row(
            args='["a", "b"]',
            kwargs='{"k": "v"}',
            options='{"queue": "q"}',
        )
        cfg = TaskRepository._row_to_config(row)
        assert cfg.args == ["a", "b"]
        assert cfg.kwargs == {"k": "v"}
        assert cfg.options == {"queue": "q"}

    def test_json_already_parsed(self) -> None:
        """JSON 字段已为 list/dict 时应直接使用。"""
        row = _make_row(
            args=["a", "b"],
            kwargs={"k": "v"},
            options={"queue": "q"},
        )
        cfg = TaskRepository._row_to_config(row)
        assert cfg.args == ["a", "b"]
        assert cfg.kwargs == {"k": "v"}

    def test_null_json_fields(self) -> None:
        """JSON 字段为 None 时应返回默认值。"""
        row = _make_row(args=None, kwargs=None, options=None)
        cfg = TaskRepository._row_to_config(row)
        assert cfg.args == []
        assert cfg.kwargs == {}
        assert cfg.options == {}

    def test_interval_config(self) -> None:
        """interval 类型应正确解析。"""
        row = _make_row(
            schedule_type="interval",
            interval_seconds=3600,
        )
        cfg = TaskRepository._row_to_config(row)
        assert cfg.schedule_type == "interval"
        assert cfg.interval_seconds == 3600
