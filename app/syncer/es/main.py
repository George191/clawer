"""ES 同步服务入口 — 独立进程运行 EsSyncWorker 将 RDS 数据同步至 Elasticsearch。

启动参数（与 syncer/main.py 风格一致）:
    --config <path>      YAML 配置文件路径（可选，覆盖环境变量配置）
    --poll <seconds>     轮询间隔（默认 10）
    --batch <n>          每次处理记录数（默认 200）
    --tables <names>     只处理指定表（逗号分隔，默认全部）
    --once                只执行一轮同步后退出（用于测试/补数）

启动示例:
    python -m app.syncer.es.main
    python -m app.syncer.es.main --tables patent --batch 500
    python -m app.syncer.es.main --config /etc/spider/es_syncer.yaml
    python -m app.syncer.es.main --once  # 单次同步
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.config.settings import settings
from app.syncer.es.config import SyncConfig
from app.syncer.es.worker import EsSyncWorker

logger = logging.getLogger(__name__)


def setup_logging(service: str = "es-syncer") -> None:
    """配置日志（与 syncer/main.py 一致）。"""
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=f"%(asctime)s [{service.upper()}] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def _parse_args() -> dict:
    """解析命令行参数（与 syncer/main.py 风格一致）。"""
    args: dict = {
        "config_path": None,
        "poll_interval": settings.es_syncer_poll_interval,
        "batch_size": settings.es_syncer_batch_size,
        "tables": None,
        "once": False,
        "startup_delay": None,
    }

    i = 0
    argv = sys.argv[1:]
    while i < len(argv):
        arg = argv[i]
        if arg == "--config" and i + 1 < len(argv):
            args["config_path"] = argv[i + 1]
            i += 2
        elif arg == "--poll" and i + 1 < len(argv):
            args["poll_interval"] = int(argv[i + 1])
            i += 2
        elif arg == "--batch" and i + 1 < len(argv):
            args["batch_size"] = int(argv[i + 1])
            i += 2
        elif arg == "--tables" and i + 1 < len(argv):
            args["tables"] = argv[i + 1]
            i += 2
        elif arg == "--once":
            args["once"] = True
            i += 1
        elif arg == "--startup-delay" and i + 1 < len(argv):
            args["startup_delay"] = int(argv[i + 1])
            i += 2
        else:
            i += 1

    return args


def _build_config(args: dict) -> SyncConfig:
    """构建同步配置。"""
    if args["config_path"]:
        config = SyncConfig.from_yaml(args["config_path"])
    else:
        config = SyncConfig.from_settings()

    # 命令行参数覆盖
    config.poll_interval = args["poll_interval"]
    config.batch_size = args["batch_size"]

    if args["tables"]:
        table_names = [t.strip() for t in args["tables"].split(",") if t.strip()]
        from app.syncer.es.config import TableSyncConfig
        config.tables = [TableSyncConfig(table_name=n) for n in table_names]

    return config


async def run() -> None:
    args = _parse_args()

    if args["startup_delay"]:
        logger.info("Waiting %ds for ES to stabilize...", args["startup_delay"])
        await asyncio.sleep(args["startup_delay"])

    config = _build_config(args)

    logger.info("=== ES Syncer Service Starting ===")
    logger.info("  RDS (Postgres): %s", _mask_pg_url(settings.pg_url))
    logger.info("  Elasticsearch:  %s", config.es_url)
    logger.info("  Tables:         %s", [t.source_table for t in config.tables])
    logger.info("  Poll interval:  %ds", config.poll_interval)
    logger.info("  Batch size:     %d", config.batch_size)
    logger.info("=" * 50)

    worker = EsSyncWorker(config=config)

    if args["once"]:
        # 单次模式：执行一轮后退出
        logger.info("[ES-Syncer] Running in --once mode")
        await worker.run_once()
        await worker.stop()
    else:
        # 持续模式
        try:
            await worker.run()
        finally:
            await worker.stop()


def _mask_pg_url(url: str) -> str:
    """脱敏 PG URL。"""
    import re
    return re.sub(r"://[^@]+@", "://***:***@", url)


def main() -> None:
    setup_logging("es-syncer")
    asyncio.run(run())


if __name__ == "__main__":
    main()
