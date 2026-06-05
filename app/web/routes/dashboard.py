"""Dashboard API — 仪表盘核心指标与告警列表。

Endpoints:
    GET /api/dashboard/metrics — 仪表盘核心指标
    GET /api/dashboard/alerts  — 最近告警列表

从真实数据源（MongoDB / Postgres / Kafka / Redis）获取数据，
数据源不可用时优雅降级返回 0 值。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

from app.config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# ══════════════════════════════════════════════════════════════════════════════
#  Unified response helper
# ══════════════════════════════════════════════════════════════════════════════


def _ok(data: Any, message: str = "success") -> dict[str, Any]:
    return {
        "code": 0,
        "data": data,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Dashboard Metrics — 真实数据源
# ══════════════════════════════════════════════════════════════════════════════


@router.get("/dashboard/metrics")
async def get_metrics() -> dict[str, Any]:
    """返回仪表盘核心指标。

    数据来源：
    - MongoDB: 任务统计 (tasks 集合) + 采集记录数
    - Postgres: ETL 各层表统计
    - Kafka: topic 延迟（通过 admin client）
    - Redis: 缓存命中状态
    """
    now = datetime.now(timezone.utc).isoformat()

    # ── MongoDB: 任务统计 ──────────────────────────────────────────────────
    tasks = await _get_task_stats()

    # ── MongoDB: 采集记录数 ────────────────────────────────────────────────
    data_volume = await _get_data_volume()

    # ── Postgres: ETL 吞吐量 ───────────────────────────────────────────────
    etl_throughput = await _get_etl_throughput()

    # ── Kafka lag ───────────────────────────────────────────────────────────
    kafka_lag = await _get_kafka_lag()

    return _ok(
        {
            "tasks": tasks,
            "etl_throughput": etl_throughput,
            "kafka_lag": kafka_lag,
            "data_volume": data_volume,
            "updated_at": now,
        }
    )


# ── 子查询函数（独立，便于降级）──────────────────────────────────────────────


async def _get_task_stats() -> dict[str, int]:
    """从 MongoDB tasks 集合统计任务状态。"""
    default = {"total": 0, "running": 0, "completed": 0, "failed": 0}
    if not settings.db_url:
        return default

    try:
        from motor.motor_asyncio import AsyncIOMotorClient

        client: AsyncIOMotorClient = AsyncIOMotorClient(settings.db_url)
        db = client[settings.db_name]
        tasks_col = db["tasks"]

        # 检查 tasks 集合是否存在
        names = await db.list_collection_names()
        if "tasks" not in names:
            client.close()
            return default

        pipeline = [
            {
                "$group": {
                    "_id": "$status",
                    "count": {"$sum": 1},
                }
            }
        ]
        cursor = tasks_col.aggregate(pipeline)
        status_map: dict[str, int] = {}
        async for doc in cursor:
            status_map[doc["_id"]] = doc["count"]

        client.close()
        return {
            "total": sum(status_map.values()),
            "running": status_map.get("running", 0),
            "completed": status_map.get("completed", 0),
            "failed": status_map.get("failed", 0),
        }
    except Exception as e:
        logger.warning("MongoDB 任务统计失败: %s", e)
        return default


async def _get_data_volume() -> dict[str, int]:
    """从 MongoDB 统计总记录数与今日增量。"""
    default = {"total": 0, "daily_increment": 0}
    if not settings.db_url:
        return default

    try:
        from motor.motor_asyncio import AsyncIOMotorClient

        client: AsyncIOMotorClient = AsyncIOMotorClient(settings.db_url)
        db = client[settings.db_name]

        # 获取所有数据集合（排除 tasks / system 集合）
        all_names = await db.list_collection_names()
        data_names = [n for n in all_names if n not in ("tasks", "system", "system.indexes")]
        if not data_names:
            client.close()
            return default

        total = 0
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

        for coll_name in data_names:
            try:
                total += await db[coll_name].count_documents({})
            except Exception:
                pass

        # 今日增量：统计 _meta.download_time 或 created_at 在今天的数据
        daily = 0
        for coll_name in data_names:
            try:
                daily += await db[coll_name].count_documents(
                    {"_meta.download_time": {"$gte": today}}
                )
            except Exception:
                try:
                    daily += await db[coll_name].count_documents(
                        {"created_at": {"$gte": today}}
                    )
                except Exception:
                    pass

        client.close()
        return {"total": total, "daily_increment": daily}
    except Exception as e:
        logger.warning("MongoDB 数据量统计失败: %s", e)
        return default


async def _get_etl_throughput() -> dict[str, Any]:
    """从 Postgres 查询 ETL 各层最近吞吐量。"""
    default = {"current": 0, "history": []}
    if not settings.pg_url or settings.pg_url == settings.__class__.model_fields["pg_url"].default:
        return default

    try:
        from app.storage.postgres_client import get_pg_client

        pg = get_pg_client()
        await pg.connect()

        # 查询最近 7 个窗口（每个 5 分钟）的插入量
        result = await pg.fetch_all("""
            WITH time_windows AS (
                SELECT generate_series(
                    date_trunc('hour', now()) - interval '30 minutes',
                    date_trunc('hour', now()),
                    interval '5 minutes'
                ) AS window_start
            ),
            all_tables AS (
                SELECT schemaname, tablename
                FROM pg_catalog.pg_tables
                WHERE schemaname IN ('rds', 'ods', 'task', 'dwd', 'dws', 'dim', 'ads')
            )
            SELECT
                tw.window_start::text AS ts,
                COALESCE(SUM(
                    (SELECT n_live_tup
                     FROM pg_stat_user_tables s
                     WHERE s.schemaname = at.schemaname AND s.relname = at.tablename)
                ), 0)::bigint AS v
            FROM time_windows tw
            CROSS JOIN all_tables at
            GROUP BY tw.window_start
            ORDER BY tw.window_start
        """)

        history = [
            {"ts": row["ts"], "v": row["v"]}
            for row in result
        ]
        current = history[-1]["v"] if history else 0

        return {"current": current, "history": history}
    except Exception as e:
        logger.warning("ETL 吞吐量查询失败: %s", e)
        return default


async def _get_kafka_lag() -> dict[str, Any]:
    """查询 Kafka 各 topic 的消息积压。"""
    default = {"total": 0, "by_layer": {}}
    if not settings.kafka_brokers:
        return default

    try:
        from aiokafka.admin import AIOKafkaAdminClient

        brokers = [b.strip() for b in settings.kafka_brokers.split(",") if b.strip()]
        admin = AIOKafkaAdminClient(bootstrap_servers=brokers)
        await admin.start()

        try:
            consumer_groups = await admin.list_consumer_groups()

            by_layer: dict[str, int] = {}
            total_lag = 0

            for group in consumer_groups:
                try:
                    group_id = group[0] if isinstance(group, tuple) else group
                    offsets = await admin.list_consumer_group_offsets(group_id)
                    # offsets is a dict of TopicPartition -> OffsetAndMetadata
                    for tp, offset_meta in offsets.items():
                        lag = offset_meta.offset if hasattr(offset_meta, 'offset') else 0
                        # Map group id to layer name
                        layer = _group_to_layer(group_id)
                        by_layer[layer] = by_layer.get(layer, 0) + lag
                        total_lag += lag
                except Exception:
                    pass

            return {"total": total_lag, "by_layer": by_layer}
        finally:
            await admin.close()
    except Exception as e:
        logger.warning("Kafka lag 查询失败: %s", e)
        return default


def _group_to_layer(group_id: str) -> str:
    """将 consumer group id 映射到 ETL 层名称。"""
    group_lower = group_id.lower()
    for layer in ("rds", "ods", "task", "dwd", "dws", "dim", "ads"):
        if layer in group_lower:
            return layer
    return group_id or "unknown"


# ══════════════════════════════════════════════════════════════════════════════
#  Dashboard Alerts
# ══════════════════════════════════════════════════════════════════════════════


@router.get("/dashboard/alerts")
async def get_alerts(limit: int = 20) -> dict[str, Any]:
    """返回最近告警列表。

    尝试从 MongoDB alerts 集合读取，无数据时返回空列表。
    """
    alerts: list[dict[str, Any]] = []

    if settings.db_url:
        try:
            from motor.motor_asyncio import AsyncIOMotorClient

            client: AsyncIOMotorClient = AsyncIOMotorClient(settings.db_url)
            db = client[settings.db_name]
            names = await db.list_collection_names()

            if "alerts" in names:
                cursor = (
                    db["alerts"]
                    .find()
                    .sort("timestamp", -1)
                    .limit(limit)
                )
                async for doc in cursor:
                    doc["_id"] = str(doc["_id"])
                    alerts.append(doc)

            client.close()
        except Exception as e:
            logger.warning("告警查询失败: %s", e)

    return _ok(alerts)