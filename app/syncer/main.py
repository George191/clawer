"""同步服务入口 — 独立进程运行 SyncWorker 将新增或修改记录推送至 Kafka。

默认按每条记录的 data_type 发送到 <SPIDER_KAFKA_TOPIC>-<type>。

启动参数：
    --template <name>    只处理指定模板（默认扫描所有模板）
    --poll <seconds>      轮询间隔（默认 10）
    --batch <n>           每次处理记录数（默认 50）
    --data-type <type>    仅同步指定类型，发送到 <SPIDER_KAFKA_TOPIC>-<type>
    --concurrency <n>     进程内并发 I/O 数（默认 10）
"""

from __future__ import annotations

import argparse
import asyncio

from app.base.kafka_config import SYNC_DATA_TYPES
from app.config.settings import settings
from app.logger import get_logger, setup_service_logging
from app.syncer.worker import SyncWorker

logger = get_logger(__name__)


def setup_logging(service: str = "syncer") -> None:
    setup_service_logging(service, settings.log_level)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template")
    parser.add_argument("--data-type", choices=SYNC_DATA_TYPES)
    parser.add_argument("--poll", type=int, default=10)
    parser.add_argument("--batch", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--startup-delay", type=int, default=0)
    args = parser.parse_args()
    if args.batch < 1 or args.concurrency < 1 or args.poll < 0 or args.startup_delay < 0:
        parser.error("batch/concurrency must be positive; poll/startup-delay must be non-negative")
    return args


async def run() -> None:
    args = parse_args()
    if args.startup_delay:
        logger.info("Waiting %ds for Kafka to stabilize...", args.startup_delay)
        await asyncio.sleep(args.startup_delay)

    worker = SyncWorker(
        poll_interval=args.poll,
        batch_size=args.batch,
        template_name=args.template,
        data_type=args.data_type,
        concurrency=args.concurrency,
    )
    try:
        await worker.run()
    finally:
        await worker.stop()


def main() -> None:
    setup_logging("syncer")
    logger.info("=== Syncer Service Starting ===")
    logger.info("  MongoDB: %s", settings.db_url)
    logger.info("  Kafka:   %s -> %s-<data_type>", settings.kafka_brokers, settings.kafka_topic)
    logger.info("=" * 40)
    asyncio.run(run())


if __name__ == "__main__":
    main()
