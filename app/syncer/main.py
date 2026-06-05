"""同步服务入口 — 独立进程运行 SyncWorker 将已下载记录推送至 Kafka。

启动参数：
    --poll <seconds>      轮询间隔（默认 10）
    --batch <n>           每次处理记录数（默认 50）
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.base.kafka import KafkaProducer
from app.base.mongo import MongoClient
from app.config.settings import settings
from app.syncer.worker import SyncWorker

logger = logging.getLogger(__name__)


def setup_logging(service: str = "syncer") -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=f"%(asctime)s [{service.upper()}] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


async def run() -> None:
    startup_delay: int | None = None
    poll_interval = 10
    batch_size = 50

    for i, arg in enumerate(sys.argv):
        if arg == "--poll" and i + 1 < len(sys.argv):
            poll_interval = int(sys.argv[i + 1])
        elif arg == "--batch" and i + 1 < len(sys.argv):
            batch_size = int(sys.argv[i + 1])
        elif arg == "--startup-delay" and i + 1 < len(sys.argv):
            startup_delay = int(sys.argv[i + 1])

    if startup_delay:
        logger.info("Waiting %ds for Kafka to stabilize...", startup_delay)
        await asyncio.sleep(startup_delay)

    worker = SyncWorker(poll_interval=poll_interval, batch_size=batch_size)
    try:
        await worker.run()
    finally:
        await worker.stop()


def main() -> None:
    setup_logging("syncer")
    logger.info("=== Syncer Service Starting ===")
    logger.info("  MongoDB: %s", settings.db_url)
    logger.info("  Kafka:   %s -> %s", settings.kafka_brokers, settings.kafka_topic)
    logger.info("=" * 40)
    asyncio.run(run())


if __name__ == "__main__":
    main()