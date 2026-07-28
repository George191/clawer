"""Celery Beat 定时调度配置 — 任务注册中心。

定义所有定时任务的执行频率和参数。
支持两种配置来源：
1. 内存默认配置（兜底，确保数据库不可用时仍可运行）
2. 数据库配置（数据驱动，用户可通过 API 维护，见 public.scheduler_tasks）

任务名约定
----------
每个 beat entry 的 key 即为 task_name，与 TaskStore 中记录的 task_name 字段对齐。
monitor.py 通过 BeatScheduleRegistry 获取需要监控的任务列表。

数据驱动加载
------------
Celery worker 启动时，CeleryAppFactory 会调用 load_from_db() 从数据库加载配置。
若数据库不可用或表未初始化，自动回退到内存默认配置。
"""

from __future__ import annotations

from typing import Any

from celery.schedules import crontab

from app.logger import get_logger

logger = get_logger(__name__)


class BeatScheduleRegistry:
    """Beat 调度任务注册中心。

    管理所有定时任务的配置，提供统一的查询接口。
    单例模式确保全局配置一致。

    配置优先级:
        数据库配置 > 内存默认配置（兜底）

    用法:
        # 同步访问（使用当前内存配置）
        registry = BeatScheduleRegistry.instance()
        schedule = registry.build_schedule()

        # 异步从数据库刷新（Celery 启动时调用）
        await registry.load_from_db()
    """

    _instance: BeatScheduleRegistry | None = None

    def __init__(self) -> None:
        self._entries: dict[str, dict] = {}
        self._db_loaded: bool = False
        self._register_default_tasks()

    @classmethod
    def instance(cls) -> BeatScheduleRegistry:
        """获取全局单例。"""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ── 任务注册（内存） ──────────────────────────────────────────────

    def register(
        self,
        name: str,
        task: str,
        schedule: crontab,
        args: tuple = (),
        kwargs: dict | None = None,
        options: dict | None = None,
    ) -> None:
        """注册一个定时任务到内存。

        Args:
            name: 任务名（= task_name，与 TaskStore 对齐）
            task: Celery task 路径
            schedule: crontab 调度表达式
            args: 位置参数
            kwargs: 关键字参数
            options: 队列、过期等选项
        """
        self._entries[name] = {
            "task": task,
            "schedule": schedule,
            "args": args,
            "kwargs": kwargs or {},
            "options": options or {},
        }

    def _register_default_tasks(self) -> None:
        """注册内置默认任务（兜底配置，与 init_scheduler_tasks.sql 种子数据一致）。"""
        # ── Google Patent 每日采集 ──
        # 每天 UTC 06:00 执行（采集前一天发布的专利）
        self.register(
            name="google_patent_daily",
            task="app.scheduler.tasks.google_patent.crawl_daily",
            schedule=crontab(hour=6, minute=0),
            options={
                "queue": "patent",
                "expires": 6 * 3600,  # 6小时后过期（避免堆积）
            },
        )

        # ── Google Patent 每周全量补采 ──
        # 每周一 UTC 03:00 执行（采集过去 7 天的专利，补漏）
        self.register(
            name="google_patent_range",
            task="app.scheduler.tasks.google_patent.crawl_date_range",
            schedule=crontab(hour=3, minute=0, day_of_week=1),
            kwargs={"days_back": 7},
            options={
                "queue": "patent",
                "expires": 12 * 3600,
            },
        )

        self._register_system_tasks()

    def _register_system_tasks(self) -> None:
        """Register scheduler infrastructure that database overrides cannot remove."""
        self.register(
            name="workspace_recurring_dispatch",
            task="app.scheduler.tasks.workspace.dispatch_due",
            schedule=crontab(minute="*"),
            options={"expires": 55},
        )

    # ── 数据库加载（数据驱动） ────────────────────────────────────────

    async def load_from_db(self, force: bool = False) -> bool:
        """从数据库加载任务配置，替换内存配置。

        数据库不可用或表未初始化时，保留现有内存配置（兜底）。

        Args:
            force: 强制重新加载（忽略已加载标志）

        Returns:
            True 表示成功从数据库加载，False 表示使用内存兜底
        """
        if self._db_loaded and not force:
            return True

        try:
            from app.scheduler.task_repository import get_task_repository

            repo = get_task_repository()
            configs = await repo.list_enabled()
            if not configs:
                logger.warning(
                    "Database returned no enabled tasks; keeping in-memory defaults"
                )
                return False

            # 替换内存配置
            self._entries = {
                cfg.task_name: cfg.to_beat_entry() for cfg in configs
            }
            self._register_system_tasks()
            self._db_loaded = True
            logger.info(
                "Loaded %d tasks from database: %s",
                len(self._entries),
                list(self._entries.keys()),
            )
            return True

        except Exception as e:
            logger.warning(
                "Failed to load tasks from database, using in-memory defaults: %s",
                e,
            )
            return False

    @property
    def is_db_loaded(self) -> bool:
        """是否已从数据库加载配置。"""
        return self._db_loaded

    # ── 查询接口 ──────────────────────────────────────────────────────

    def build_schedule(self) -> dict:
        """构建 Celery beat_schedule 字典。"""
        return dict(self._entries)

    def get_task_names(self) -> list[str]:
        """获取所有已注册的任务名列表。"""
        return list(self._entries.keys())

    def get_task_config(self, name: str) -> dict | None:
        """获取指定任务的配置。"""
        return self._entries.get(name)

    def __len__(self) -> int:
        return len(self._entries)


# ══════════════════════════════════════════════════════════════════════
#  兼容函数（保持向后兼容）
# ══════════════════════════════════════════════════════════════════════

def build_beat_schedule() -> dict:
    """构建 Beat 调度配置（兼容旧接口）。"""
    return BeatScheduleRegistry.instance().build_schedule()


def get_monitored_task_names() -> list[str]:
    """获取需要监控的任务名列表（兼容旧接口）。"""
    return BeatScheduleRegistry.instance().get_task_names()


async def load_beat_schedule_from_db() -> bool:
    """从数据库加载 Beat 调度配置（兼容旧接口）。

    Returns:
        True 表示成功加载
    """
    return await BeatScheduleRegistry.instance().load_from_db()
