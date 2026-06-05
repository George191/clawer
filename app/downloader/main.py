"""下载服务入口 — 独立进程运行 DownloadWorker 监听 MongoDB 并下载资源。

启动参数：
    --poll <seconds>      轮询间隔（默认 10）
    --batch <n>           每次处理记录数（默认 50）
"""

from __future__ import annotations

import asyncio
import logging
import sys

from app.base.mongo import MongoClient
from app.config.settings import settings
from app.downloader.worker import DownloadWorker

logger = logging.getLogger(__name__)


def setup_logging(service: str = "downloader") -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format=f"%(asctime)s [{service.upper()}] %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


async def run() -> None:
    poll_interval = 10
    batch_size = 50

    for i, arg in enumerate(sys.argv):
        if arg == "--poll" and i + 1 < len(sys.argv):
            poll_interval = int(sys.argv[i + 1])
        elif arg == "--batch" and i + 1 < len(sys.argv):
            batch_size = int(sys.argv[i + 1])

    worker = DownloadWorker(poll_interval=poll_interval, batch_size=batch_size)
    try:
        await worker.run()
    finally:
        await worker.stop()


def main() -> None:
    setup_logging("downloader")
    logger.info("=== Downloader Service Starting ===")
    logger.info("  MongoDB: %s", settings.db_url)
    logger.info("  MinIO:   %s", settings.minio_endpoint)
    logger.info("=" * 40)
    asyncio.run(run())


if __name__ == "__main__":
    main()