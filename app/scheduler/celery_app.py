"""Celery 应用实例 — 任务调度核心。

使用 Redis 作为 Broker 和 Result Backend，支持 Beat 定时调度。
项目原有代码为 async 架构，Celery task 内部通过 asyncio.run() 桥接。

启动 Worker:
    celery -A app.scheduler.celery_app worker --loglevel=info

启动 Beat 调度器:
    celery -A app.scheduler.celery_app beat --loglevel=info

同时启动 Worker + Beat:
    celery -A app.scheduler.celery_app worker --beat --loglevel=info
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil

from app.config.settings import settings

logger = logging.getLogger(__name__)

# ── Celery 导入（延迟处理，允许未安装时模块仍可被 import 检查） ──

try:
    from celery import Celery
except ImportError as e:
    raise ImportError(
        "Celery 未安装，请执行: pip install celery redis"
    ) from e


class CeleryAppFactory:
    """Celery 应用工厂。

    封装 Celery 实例的创建、配置、任务发现和 Beat 调度注册。

    用法:
        factory = CeleryAppFactory()
        app = factory.create()
    """

    def __init__(self, app_name: str = "spider") -> None:
        self._app_name = app_name
        self._app: Celery | None = None

    # ── 配置 ──────────────────────────────────────────────────────────

    def _get_broker_url(self) -> str:
        """从项目 settings 推导 Celery broker URL。"""
        return settings.redis_url

    def _get_result_backend(self) -> str:
        """结果后端，默认与 broker 共用 Redis。"""
        return settings.redis_url

    def _build_config(self) -> dict:
        """构建 Celery 配置字典。"""
        return {
            # ── 序列化 ──
            "task_serializer": "json",
            "result_serializer": "json",
            "accept_content": ["json"],

            # ── 时区 ──
            "timezone": "UTC",
            "enable_utc": True,

            # ── 任务执行 ──
            "task_acks_late": True,                    # 任务完成后才确认（故障恢复）
            "task_reject_on_worker_lost": True,        # Worker 异常退出时拒绝任务（重新入队）
            "worker_prefetch_multiplier": 1,           # 每次只预取 1 个任务（长任务场景）

            # ── 重试 ──
            "task_default_retry_delay": 60,            # 默认重试间隔 60s
            "task_default_max_retries": 3,             # 默认最多重试 3 次

            # ── 结果过期 ──
            "result_expires": 7 * 24 * 3600,           # 结果保留 7 天

            # ── Beat 调度器 ──
            "beat_scheduler": "celery.beat:PersistentScheduler",  # 持久化调度（文件）
            "beat_schedule_filename": "celerybeat-schedule",      # 调度状态文件

            # ── 监控 ──
            "worker_send_task_events": True,           # 发送任务事件（供监控消费）
            "task_send_sent_event": True,              # 发送任务下发事件
        }

    # ── 任务发现 ──────────────────────────────────────────────────────

    def _discover_task_modules(self) -> list[str]:
        """自动发现 tasks/ 下的所有子包作为 Celery task 模块。

        约定: app/scheduler/tasks/<name>/tasks.py 会被自动注册。
        新增任务只需创建对应子包，无需修改本文件。

        注意: 只扫描文件系统，不实际导入模块。
        模块导入由 Celery worker 启动时按 include 列表自动执行，
        避免在 celery_app 加载阶段触发循环导入（tasks.py 依赖 app）。
        """
        modules: list[str] = []
        try:
            import app.scheduler.tasks as tasks_pkg
        except ImportError:
            return modules

        for module_info in pkgutil.iter_modules(tasks_pkg.__path__):
            if not module_info.ispkg:
                continue
            task_module = f"app.scheduler.tasks.{module_info.name}.tasks"
            # 校验 tasks.py 是否存在（避免 include 不存在的模块）
            try:
                importlib.util.find_spec(task_module)
            except (ImportError, ValueError):
                continue
            modules.append(task_module)
            logger.debug("Discovered task module: %s", task_module)

        return modules

    # ── 应用创建 ──────────────────────────────────────────────────────

    def create(self) -> Celery:
        """创建并配置 Celery 应用实例。"""
        if self._app is not None:
            return self._app

        self._app = Celery(
            self._app_name,
            broker=self._get_broker_url(),
            backend=self._get_result_backend(),
        )

        # 应用基础配置
        self._app.conf.update(self._build_config())

        # 注册 task 模块和 Beat 调度
        self._register_modules_and_schedule()

        return self._app

    def _register_modules_and_schedule(self) -> None:
        """注册 task 模块和 Beat 调度配置。

        分两步:
        1. 自动发现并 import task 模块（触发 @app.task 装饰器注册）
        2. 从 BeatScheduleRegistry 加载调度配置（避免循环依赖）

        数据库配置加载通过信号在 worker/beat 启动时异步执行，
        此处先用内存默认配置兜底，确保 app 可立即使用。
        """
        assert self._app is not None

        # 1. 自动发现 task 模块
        discovered = self._discover_task_modules()
        self._app.conf.update(include=discovered)
        logger.info("Celery task modules registered: %d", len(discovered))

        # 2. 加载 Beat 调度（内存默认配置，数据库配置在启动时通过信号加载）
        from app.scheduler.beat_schedule import BeatScheduleRegistry
        registry = BeatScheduleRegistry.instance()
        self._app.conf.beat_schedule = registry.build_schedule()
        logger.info("Beat schedule registered (in-memory defaults): %d tasks", len(registry))

        # 3. 注册信号：worker/beat 启动时从数据库加载配置
        self._register_db_load_signal()

    def _register_db_load_signal(self) -> None:
        """注册信号，在 worker/beat 启动时从数据库加载任务配置。

        数据库不可用时自动回退到内存默认配置。
        """
        from celery.signals import worker_init, beat_init

        @worker_init.connect
        def _on_worker_init(**kwargs: object) -> None:
            self._load_schedule_from_db_sync()

        @beat_init.connect
        def _on_beat_init(**kwargs: object) -> None:
            self._load_schedule_from_db_sync()

    @staticmethod
    def _load_schedule_from_db_sync() -> None:
        """同步执行数据库配置加载（在 Celery 信号回调中使用）。"""
        import asyncio

        from app.scheduler.beat_schedule import BeatScheduleRegistry

        async def _load() -> bool:
            return await BeatScheduleRegistry.instance().load_from_db(force=True)

        try:
            loaded = asyncio.run(_load())
            if loaded:
                registry = BeatScheduleRegistry.instance()
                app.conf.beat_schedule = registry.build_schedule()
                logger.info(
                    "Beat schedule reloaded from database: %d tasks",
                    len(registry),
                )
        except Exception as e:
            logger.warning("Failed to load beat schedule from database: %s", e)


# ══════════════════════════════════════════════════════════════════════
#  模块级 Celery 实例（Celery 要求模块级变量）
# ══════════════════════════════════════════════════════════════════════

app = CeleryAppFactory().create()
