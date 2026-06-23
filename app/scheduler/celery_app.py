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

import logging

from app.config.settings import settings

logger = logging.getLogger(__name__)

# ── Celery 导入（延迟处理，允许未安装时模块仍可被 import 检查） ──

try:
    from celery import Celery
    from celery.schedules import crontab
except ImportError:
    raise ImportError(
        "Celery 未安装，请执行: pip install celery redis"
    )


# ══════════════════════════════════════════════════════════════════════
#  Broker / Backend 配置
# ══════════════════════════════════════════════════════════════════════

def _get_broker_url() -> str:
    """从项目 settings 推导 Celery broker URL。"""
    return settings.redis_url


def _get_result_backend() -> str:
    """结果后端，默认与 broker 共用 Redis。"""
    return settings.redis_url


# ══════════════════════════════════════════════════════════════════════
#  Celery 实例
# ══════════════════════════════════════════════════════════════════════

app = Celery(
    "spider",
    broker=_get_broker_url(),
    backend=_get_result_backend(),
    include=[
        "app.scheduler.tasks.google_patent_daily",
    ],
)

app.conf.update(
    # ── 序列化 ──
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # ── 时区 ──
    timezone="UTC",
    enable_utc=True,

    # ── 任务执行 ──
    task_acks_late=True,                    # 任务完成后才确认（故障恢复）
    task_reject_on_worker_lost=True,        # Worker 异常退出时拒绝任务（重新入队）
    worker_prefetch_multiplier=1,           # 每次只预取 1 个任务（长任务场景）

    # ── 重试 ──
    task_default_retry_delay=60,            # 默认重试间隔 60s
    task_default_max_retries=3,             # 默认最多重试 3 次

    # ── 结果过期 ──
    result_expires=7 * 24 * 3600,           # 结果保留 7 天

    # ── Beat 调度器 ──
    beat_scheduler="celery.beat:PersistentScheduler",  # 持久化调度（文件）
    beat_schedule_filename="celerybeat-schedule",      # 调度状态文件

    # ── 监控 ──
    worker_send_task_events=True,           # 发送任务事件（供监控消费）
    task_send_sent_event=True,              # 发送任务下发事件
)


# ══════════════════════════════════════════════════════════════════════
#  Beat 定时任务注册
# ══════════════════════════════════════════════════════════════════════

def _register_beat_schedule() -> None:
    """注册 Beat 定时任务。

    从 beat_schedule.py 加载调度配置，避免循环依赖。
    """
    from app.scheduler.beat_schedule import build_beat_schedule

    app.conf.beat_schedule = build_beat_schedule()
    logger.info("Beat schedule registered: %d tasks", len(app.conf.beat_schedule))


_register_beat_schedule()
