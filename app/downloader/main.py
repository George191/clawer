"""下载服务入口 — 独立进程消费 MongoDB CDC 事件，并从 MongoDB 补偿遗漏记录。

启动参数：
    --template <name>    只处理指定模板（默认扫描所有模板）
    --datatype <type>    按模板及集合路由解析业务类型（默认所有类型）
    --poll <seconds>      MongoDB 补偿间隔及待处理记录最小年龄（默认 60）
    --batch <n>           每次处理记录数（默认 50）
"""

from __future__ import annotations

import argparse
import asyncio

from app.config.settings import settings
from app.downloader.worker import DownloadWorker
from app.logger import get_logger, setup_service_logging
from app.logger.task_format import redact_connection_userinfo

logger = get_logger(__name__)


def setup_logging(service: str = "downloader") -> None:
    setup_service_logging(service, settings.log_level)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Reject unknown scope flags before any records can be claimed."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--template")
    parser.add_argument("--datatype", "--data-type", dest="data_type")
    parser.add_argument("--poll", type=int, default=60)
    parser.add_argument("--batch", type=int, default=50)
    args = parser.parse_args(argv)
    if args.poll < 0 or args.batch < 1:
        parser.error("poll must be non-negative and batch must be positive")
    return args


async def run() -> None:
    args = parse_args()
    worker = DownloadWorker(
        poll_interval=args.poll,
        batch_size=args.batch,
        template_name=args.template,
        data_type=args.data_type,
    )
    try:
        await worker.run()
    finally:
        await worker.stop()


def main() -> None:
    setup_logging("downloader")
    logger.info("=== Downloader Service Starting ===")
    logger.info("  MongoDB: %s", redact_connection_userinfo(settings.db_url))
    logger.info("  MinIO:   %s", settings.minio_endpoint)
    logger.info("=" * 40)
    asyncio.run(run())


if __name__ == "__main__":
    main()
