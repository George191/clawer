"""调度任务配置 — 数据访问层。

基于 PostgreSQL public.scheduler_tasks 表，提供 CRUD 操作。
BeatScheduleRegistry 通过本模块从数据库加载任务配置。

表结构见 scripts/init_scheduler_tasks.sql
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.logger import get_logger
from app.storage.postgres_client import PostgresClient, get_pg_client

logger = get_logger(__name__)

TABLE_NAME = "public.scheduler_tasks"
_DDL_PATH = Path(__file__).resolve().parent.parent.parent.parent / "scripts" / "init_scheduler_tasks.sql"
_READY = False


# ══════════════════════════════════════════════════════════════════════
#  数据模型
# ══════════════════════════════════════════════════════════════════════

@dataclass
class TaskConfig:
    """调度任务配置（领域模型）。"""

    task_name: str
    task_path: str
    schedule_type: str = "crontab"  # crontab / interval

    # crontab 字段
    cron_minute: str = "*"
    cron_hour: str = "*"
    cron_day_of_week: str = "*"
    cron_day_of_month: str = "*"
    cron_month_of_year: str = "*"

    # interval 字段
    interval_seconds: int | None = None

    # 任务参数
    args: list[Any] = field(default_factory=list)
    kwargs: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)

    # 状态与审计
    enabled: bool = True
    description: str | None = None
    updated_by: str | None = None

    # 只读元数据（查询时填充）
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_beat_entry(self) -> dict[str, Any]:
        """转换为 Celery beat_schedule entry 格式。"""
        from celery.schedules import crontab
        from celery.schedules import schedule as interval_schedule

        if self.schedule_type == "interval":
            entry_schedule = interval_schedule(run_every=self.interval_seconds or 60)
        else:
            entry_schedule = crontab(
                minute=self.cron_minute,
                hour=self.cron_hour,
                day_of_week=self.cron_day_of_week,
                day_of_month=self.cron_day_of_month,
                month_of_year=self.cron_month_of_year,
            )

        return {
            "task": self.task_path,
            "schedule": entry_schedule,
            "args": tuple(self.args),
            "kwargs": self.kwargs,
            "options": self.options,
        }


# ══════════════════════════════════════════════════════════════════════
#  TaskRepository — 数据访问层
# ══════════════════════════════════════════════════════════════════════

class TaskRepository:
    """调度任务配置仓库。

    提供 public.scheduler_tasks 表的 CRUD 操作。

    用法:
        repo = TaskRepository()
        await repo.ensure_table()
        tasks = await repo.list_enabled()
        await repo.create(TaskConfig(...))
    """

    def __init__(self, pg: PostgresClient | None = None) -> None:
        self._pg = pg

    # ── 依赖管理 ──────────────────────────────────────────────────────

    def _get_pg(self) -> PostgresClient:
        if self._pg is None:
            self._pg = get_pg_client()
        return self._pg

    # ── 表初始化 ──────────────────────────────────────────────────────

    async def ensure_table(self) -> None:
        """确保表存在（幂等，执行 init_scheduler_tasks.sql）。"""
        global _READY
        if _READY:
            return

        pg = self._get_pg()
        await pg.connect()

        # 检查表是否已存在
        row = await pg.fetch_one(
            "SELECT to_regclass(:table_name) AS reg",
            {"table_name": TABLE_NAME},
        )
        if row and row.get("reg"):
            _READY = True
            return

        # 执行初始化 SQL
        if _DDL_PATH.exists():
            ddl = _DDL_PATH.read_text(encoding="utf-8")
            await pg.init_schema([ddl])
            logger.info("scheduler_tasks table initialized with seed data")
        else:
            raise FileNotFoundError(f"DDL script not found: {_DDL_PATH}")

        _READY = True

    # ── 查询接口 ──────────────────────────────────────────────────────

    async def list_all(self) -> list[TaskConfig]:
        """查询所有任务配置。"""
        await self.ensure_table()
        rows = await self._get_pg().fetch_all(
            f"""
            SELECT id, task_name, task_path, description,
                   schedule_type, cron_minute, cron_hour, cron_day_of_week,
                   cron_day_of_month, cron_month_of_year, interval_seconds,
                   args, kwargs, options, enabled,
                   created_at, updated_at, updated_by
            FROM {TABLE_NAME}
            ORDER BY task_name
            """
        )
        return [self._row_to_config(r) for r in rows]

    async def list_enabled(self) -> list[TaskConfig]:
        """查询所有启用的任务配置（Beat 调度器使用）。"""
        await self.ensure_table()
        rows = await self._get_pg().fetch_all(
            f"""
            SELECT id, task_name, task_path, description,
                   schedule_type, cron_minute, cron_hour, cron_day_of_week,
                   cron_day_of_month, cron_month_of_year, interval_seconds,
                   args, kwargs, options, enabled,
                   created_at, updated_at, updated_by
            FROM {TABLE_NAME}
            WHERE enabled = TRUE
            ORDER BY task_name
            """
        )
        return [self._row_to_config(r) for r in rows]

    async def get_by_name(self, task_name: str) -> TaskConfig | None:
        """按任务名查询。"""
        await self.ensure_table()
        row = await self._get_pg().fetch_one(
            f"""
            SELECT id, task_name, task_path, description,
                   schedule_type, cron_minute, cron_hour, cron_day_of_week,
                   cron_day_of_month, cron_month_of_year, interval_seconds,
                   args, kwargs, options, enabled,
                   created_at, updated_at, updated_by
            FROM {TABLE_NAME}
            WHERE task_name = :task_name
            """,
            {"task_name": task_name},
        )
        return self._row_to_config(row) if row else None

    # ── 写入接口 ──────────────────────────────────────────────────────

    async def create(self, config: TaskConfig, updated_by: str = "user") -> TaskConfig:
        """创建任务配置。

        Raises:
            ValueError: task_name 已存在
        """
        await self.ensure_table()
        existing = await self.get_by_name(config.task_name)
        if existing is not None:
            raise ValueError(f"任务名已存在: {config.task_name}")

        await self._get_pg().execute(
            f"""
            INSERT INTO {TABLE_NAME} (
                task_name, task_path, description,
                schedule_type, cron_minute, cron_hour, cron_day_of_week,
                cron_day_of_month, cron_month_of_year, interval_seconds,
                args, kwargs, options, enabled, updated_by
            ) VALUES (
                :task_name, :task_path, :description,
                :schedule_type, :cron_minute, :cron_hour, :cron_day_of_week,
                :cron_day_of_month, :cron_month_of_year, :interval_seconds,
                CAST(:args AS jsonb), CAST(:kwargs AS jsonb), CAST(:options AS jsonb),
                :enabled, :updated_by
            )
            """,
            {
                "task_name": config.task_name,
                "task_path": config.task_path,
                "description": config.description,
                "schedule_type": config.schedule_type,
                "cron_minute": config.cron_minute,
                "cron_hour": config.cron_hour,
                "cron_day_of_week": config.cron_day_of_week,
                "cron_day_of_month": config.cron_day_of_month,
                "cron_month_of_year": config.cron_month_of_year,
                "interval_seconds": config.interval_seconds,
                "args": json.dumps(config.args),
                "kwargs": json.dumps(config.kwargs),
                "options": json.dumps(config.options),
                "enabled": config.enabled,
                "updated_by": updated_by,
            },
        )
        logger.info("Task config created: %s by %s", config.task_name, updated_by)
        result = await self.get_by_name(config.task_name)
        assert result is not None
        return result

    async def update(
        self,
        task_name: str,
        updates: dict[str, Any],
        updated_by: str = "user",
    ) -> TaskConfig | None:
        """更新任务配置（部分更新）。

        Args:
            task_name: 任务名
            updates: 可更新字段（task_path/description/schedule_type/cron_*/interval_seconds/
                     args/kwargs/options/enabled）
            updated_by: 操作人
        """
        await self.ensure_table()

        allowed_fields = {
            "task_path", "description", "schedule_type",
            "cron_minute", "cron_hour", "cron_day_of_week",
            "cron_day_of_month", "cron_month_of_year", "interval_seconds",
            "enabled",
        }
        json_fields = {"args", "kwargs", "options"}

        set_parts: list[str] = []
        params: dict[str, Any] = {"updated_by": updated_by}

        for key, value in updates.items():
            if key in allowed_fields:
                set_parts.append(f"{key} = :{key}")
                params[key] = value
            elif key in json_fields:
                set_parts.append(f"{key} = CAST(:{key} AS jsonb)")
                params[key] = json.dumps(value)

        if not set_parts:
            raise ValueError("没有可更新的字段")

        set_parts.append("updated_by = :updated_by")
        set_clause = ", ".join(set_parts)

        await self._get_pg().execute(
            f"UPDATE {TABLE_NAME} SET {set_clause} WHERE task_name = :task_name",
            {**params, "task_name": task_name},
        )
        logger.info("Task config updated: %s by %s", task_name, updated_by)
        return await self.get_by_name(task_name)

    async def delete(self, task_name: str, updated_by: str = "user") -> bool:
        """删除任务配置。"""
        await self.ensure_table()
        await self._get_pg().execute(
            f"DELETE FROM {TABLE_NAME} WHERE task_name = :task_name",
            {"task_name": task_name},
        )
        logger.info("Task config deleted: %s by %s", task_name, updated_by)
        return True

    async def toggle(self, task_name: str, enabled: bool, updated_by: str = "user") -> TaskConfig | None:
        """启用/禁用任务。"""
        return await self.update(
            task_name, {"enabled": enabled}, updated_by=updated_by
        )

    # ── 内部工具 ──────────────────────────────────────────────────────

    @staticmethod
    def _row_to_config(row: dict[str, Any]) -> TaskConfig:
        """数据库行转换为 TaskConfig。"""
        return TaskConfig(
            id=row.get("id"),
            task_name=row["task_name"],
            task_path=row["task_path"],
            description=row.get("description"),
            schedule_type=row.get("schedule_type", "crontab"),
            cron_minute=row.get("cron_minute", "*"),
            cron_hour=row.get("cron_hour", "*"),
            cron_day_of_week=row.get("cron_day_of_week", "*"),
            cron_day_of_month=row.get("cron_day_of_month", "*"),
            cron_month_of_year=row.get("cron_month_of_year", "*"),
            interval_seconds=row.get("interval_seconds"),
            args=_parse_json(row.get("args"), list),
            kwargs=_parse_json(row.get("kwargs"), dict),
            options=_parse_json(row.get("options"), dict),
            enabled=row.get("enabled", True),
            updated_by=row.get("updated_by"),
            created_at=str(row["created_at"]) if row.get("created_at") else None,
            updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
        )


def _parse_json(value: Any, expected_type: type) -> Any:
    """解析 JSON 字段（兼容已解析的 dict/list 和字符串）。"""
    if value is None:
        return expected_type()
    if isinstance(value, expected_type):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return expected_type()
    return expected_type()


# ══════════════════════════════════════════════════════════════════════
#  全局单例
# ══════════════════════════════════════════════════════════════════════

_repo: TaskRepository | None = None


def get_task_repository() -> TaskRepository:
    """获取全局 TaskRepository 单例。"""
    global _repo
    if _repo is None:
        _repo = TaskRepository()
    return _repo
