"""ETL 服务入口 — 独立进程运行六层 Worker。

用法：
    python -m app.etl.main --layer rds     # 启动 RDS 层
    python -m app.etl.main --layer ods     # 启动 ODS 层
    python -m app.etl.main --layer task     # 启动 TASK 层
    python -m app.etl.main --layer dwd     # 启动 DWD 层
    python -m app.etl.main --layer dws     # 启动 DWS 层
    python -m app.etl.main --layer dim     # 启动 DIM 层
    python -m app.etl.main --layer all     # 启动全部六层
    python -m app.etl.main --init-schema   # 仅初始化数据库 Schema

数据流链路：
    crawl → RDS → ODS → TASK → ADS (应用层)
                    ↘ DWD → DWS → ADS (应用层)
    DIM 维度层独立消费 ODS 数据，维护字典表
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.config.settings import settings
from app.etl.ts_rds import TsRds
from app.etl.ts_ods import TsOds
from app.etl.ts_task import TsTask
from app.etl.ts_dwd import TsDwd
from app.etl.ts_dws import TsDws
from app.etl.ts_dim import TsDim
from app.storage.postgres_client import get_pg_client

logger = logging.getLogger(__name__)


def setup_logging(service: str = "etl") -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=f"%(asctime)s [{service.upper()}] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

def _mask_url(url: str) -> str:
    import re
    return re.sub(r"://[^@]+@", "://***:***@", url)


def _preflight_check() -> None:
    errors: list[str] = []

    if not settings.kafka_brokers:
        errors.append("SPIDER_KAFKA_BROKERS 未配置 — ETL 管道依赖 Kafka")
    if not settings.pg_url or settings.pg_url == settings.__class__.model_fields["pg_url"].default:
        errors.append("SPIDER_PG_URL 未配置 — ETL 管道依赖 Postgres")

    if errors:
        print("=" * 60, file=sys.stderr)
        print("ETL 启动前置检查 FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print("请检查 .env 文件中的以下配置项:", file=sys.stderr)
        print("  SPIDER_KAFKA_BROKERS  — Kafka Broker 地址 (如 kafka:9092)", file=sys.stderr)
        print("  SPIDER_PG_URL         — Postgres 连接 URL", file=sys.stderr)
        sys.exit(1)


async def _run_layer(worker_class: type, layer_name: str) -> None:
    setup_logging(f"etl-{layer_name}")
    _preflight_check()

    logger.info("=== ETL %s Worker Starting ===", layer_name.upper())

    worker = worker_class()
    try:
        await worker.run()
    except ConnectionError:
        logger.critical("%s Worker 因连接失败退出，请检查上述错误日志", layer_name.upper())
        sys.exit(1)
    finally:
        await worker.stop()


async def run_rds() -> None:
    await _run_layer(TsRds, "rds")


async def run_ods() -> None:
    await _run_layer(TsOds, "ods")


async def run_task() -> None:
    await _run_layer(TsTask, "task")


async def run_dwd() -> None:
    await _run_layer(TsDwd, "dwd")


async def run_dws() -> None:
    await _run_layer(TsDws, "dws")


async def run_dim() -> None:
    await _run_layer(TsDim, "dim")


async def run_all() -> None:
    setup_logging("etl-all")
    _preflight_check()

    logger.info("=== ETL All Layers Starting ===")
    logger.info("  Postgres: %s", _mask_url(settings.pg_url))
    logger.info("  RDS: %s → %s", settings.etl_raw_topic, settings.etl_rds_topic)
    logger.info("  ODS: %s → %s", settings.etl_rds_topic, settings.etl_ods_topic)
    logger.info("  TASK: %s → %s", settings.etl_task_topic, settings.etl_ads_topic)
    logger.info("  DWD: %s → %s", settings.etl_ods_topic, settings.etl_dwd_topic)
    logger.info("  DWS: %s → %s", settings.etl_dwd_topic, settings.etl_dws_topic)
    logger.info("  DIM: %s → (dimension tables)", settings.etl_ods_topic)
    logger.info("=" * 50)

    rds = TsRds()
    ods = TsOds()
    task = TsTask()
    dwd = TsDwd()
    dws = TsDws()
    dim = TsDim()

    try:
        results = await asyncio.gather(
            rds.run(),
            ods.run(),
            task.run(),
            dwd.run(),
            dws.run(),
            dim.run(),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logger.critical("Worker 异常退出: %s", result)
        logger.critical("全部 Worker 已退出")
        sys.exit(1)
    finally:
        await asyncio.gather(
            rds.stop(), ods.stop(), task.stop(),
            dwd.stop(), dws.stop(), dim.stop(),
            return_exceptions=True,
        )


def main() -> None:

    for i, arg in enumerate(sys.argv):
        if arg == "--layer" and i + 1 < len(sys.argv):
            layer = sys.argv[i + 1].lower()

    runners: dict[str, callable] = {
        "rds": run_rds,
        "ods": run_ods,
        "task": run_task,
        "dwd": run_dwd,
        "dws": run_dws,
        "dim": run_dim,
        "all": run_all,
    }

    runner = runners.get(layer)
    if runner is None:
        print(f"Unknown layer: {layer}. Expected: rds, ods, task, dwd, dws, dim, all", file=sys.stderr)
        sys.exit(1)

    asyncio.run(runner())


if __name__ == "__main__":
    main()