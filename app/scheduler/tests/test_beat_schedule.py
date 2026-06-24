"""BeatScheduleRegistry — 单元测试。

测试 BeatScheduleRegistry 类的注册、查询、单例、数据库加载功能。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from celery.schedules import crontab

from app.scheduler.beat_schedule import BeatScheduleRegistry


class TestBeatScheduleRegistry:
    """测试 BeatScheduleRegistry 类。"""

    def test_instance_returns_singleton(self) -> None:
        """instance() 应返回同一单例。"""
        r1 = BeatScheduleRegistry.instance()
        r2 = BeatScheduleRegistry.instance()
        assert r1 is r2

    def test_default_tasks_registered(self) -> None:
        """默认应注册 google_patent_daily 和 google_patent_range。"""
        registry = BeatScheduleRegistry.instance()
        names = registry.get_task_names()
        assert "google_patent_daily" in names
        assert "google_patent_range" in names

    def test_build_schedule_returns_dict(self) -> None:
        registry = BeatScheduleRegistry.instance()
        schedule = registry.build_schedule()
        assert isinstance(schedule, dict)
        assert len(schedule) > 0

    def test_each_entry_has_required_fields(self) -> None:
        """每个 entry 必须包含 task / schedule / options。"""
        registry = BeatScheduleRegistry.instance()
        schedule = registry.build_schedule()
        for name, entry in schedule.items():
            assert "task" in entry, f"{name} 缺少 task 字段"
            assert "schedule" in entry, f"{name} 缺少 schedule 字段"
            assert "options" in entry, f"{name} 缺少 options 字段"

    def test_get_task_config(self) -> None:
        """get_task_config 应返回指定任务的配置。"""
        registry = BeatScheduleRegistry.instance()
        config = registry.get_task_config("google_patent_daily")
        assert config is not None
        assert config["task"] == "app.scheduler.tasks.google_patent.crawl_daily"

    def test_get_task_config_not_found(self) -> None:
        """不存在的任务名应返回 None。"""
        registry = BeatScheduleRegistry.instance()
        assert registry.get_task_config("non_existent") is None

    def test_register_new_task(self) -> None:
        """register() 应能注册新任务到默认任务之上。"""
        registry = BeatScheduleRegistry()
        initial_count = len(registry)
        registry.register(
            name="test_task",
            task="app.test.task",
            schedule=crontab(hour=12),
            options={"queue": "test"},
        )
        assert "test_task" in registry.get_task_names()
        assert len(registry) == initial_count + 1

    def test_len_returns_entry_count(self) -> None:
        """__len__ 应返回已注册任务数。"""
        registry = BeatScheduleRegistry.instance()
        assert len(registry) >= 2


class TestBeatScheduleRegistryDbLoad:
    """测试 BeatScheduleRegistry 的数据库加载功能。"""

    @pytest.mark.asyncio
    async def test_load_from_db_success(self) -> None:
        """数据库加载成功时应替换内存配置。"""
        from app.scheduler.task_repository import TaskConfig

        registry = BeatScheduleRegistry()
        assert registry.is_db_loaded is False

        mock_configs = [
            TaskConfig(
                task_name="db_task_1",
                task_path="app.test.task1",
                cron_hour="6",
                options={"queue": "q1"},
            ),
            TaskConfig(
                task_name="db_task_2",
                task_path="app.test.task2",
                schedule_type="interval",
                interval_seconds=3600,
            ),
        ]

        with patch("app.scheduler.task_repository.get_task_repository") as mock_get_repo:
            mock_repo = MagicMock()
            mock_repo.list_enabled = AsyncMock(return_value=mock_configs)
            mock_get_repo.return_value = mock_repo

            result = await registry.load_from_db()

        assert result is True
        assert registry.is_db_loaded is True
        assert set(registry.get_task_names()) == {"db_task_1", "db_task_2"}
        # 内存默认配置应被替换
        assert "google_patent_daily" not in registry.get_task_names()

    @pytest.mark.asyncio
    async def test_load_from_db_empty_keeps_defaults(self) -> None:
        """数据库返回空列表时应保留内存默认配置。"""
        registry = BeatScheduleRegistry()
        initial_count = len(registry)

        with patch("app.scheduler.task_repository.get_task_repository") as mock_get_repo:
            mock_repo = MagicMock()
            mock_repo.list_enabled = AsyncMock(return_value=[])
            mock_get_repo.return_value = mock_repo

            result = await registry.load_from_db()

        assert result is False
        assert registry.is_db_loaded is False
        assert len(registry) == initial_count

    @pytest.mark.asyncio
    async def test_load_from_db_failure_keeps_defaults(self) -> None:
        """数据库异常时应保留内存默认配置。"""
        registry = BeatScheduleRegistry()
        initial_count = len(registry)

        with patch("app.scheduler.task_repository.get_task_repository") as mock_get_repo:
            mock_repo = MagicMock()
            mock_repo.list_enabled = AsyncMock(side_effect=Exception("DB error"))
            mock_get_repo.return_value = mock_repo

            result = await registry.load_from_db()

        assert result is False
        assert registry.is_db_loaded is False
        assert len(registry) == initial_count

    @pytest.mark.asyncio
    async def test_load_from_db_skips_when_already_loaded(self) -> None:
        """已加载时应跳过重复加载（除非 force=True）。"""
        from app.scheduler.task_repository import TaskConfig

        registry = BeatScheduleRegistry()

        mock_configs = [
            TaskConfig(task_name="db_task", task_path="app.test.task"),
        ]

        with patch("app.scheduler.task_repository.get_task_repository") as mock_get_repo:
            mock_repo = MagicMock()
            mock_repo.list_enabled = AsyncMock(return_value=mock_configs)
            mock_get_repo.return_value = mock_repo

            # 第一次加载
            await registry.load_from_db()
            assert registry.is_db_loaded is True
            assert len(registry) == 1

            # 修改 mock 返回值
            mock_repo.list_enabled = AsyncMock(return_value=[
                TaskConfig(task_name="new_task", task_path="app.test.new"),
            ])

            # 不 force，应跳过
            await registry.load_from_db()
            assert len(registry) == 1
            assert "new_task" not in registry.get_task_names()

            # force=True，应重新加载
            await registry.load_from_db(force=True)
            assert len(registry) == 1
            assert "new_task" in registry.get_task_names()
