"""监控 WebSocket — 实时推送模拟日志行。

Endpoints:
    WS /api/monitor/ws — 定时推送 JSON 格式日志行

每条日志包含字段：timestamp, level, source, message。
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

# 模拟日志源列表
_LOG_SOURCES = [
    "spider.google_patent",
    "etl.rds",
    "etl.ods",
    "etl.dwd",
    "downloader.http",
    "syncer.kafka",
    "anti_crawl.proxy",
    "quality.validator",
]

# 模拟日志模板 (level, template_string)
# 注意: 占位符名不能与外面 random.choice 变量冲突
_LOG_TEMPLATES = [
    ("INFO", "Page {page} crawled successfully, found {count} records"),
    ("DEBUG", "Request headers: {req_headers}"),
    ("INFO", "Record saved to MongoDB: {rec_id}"),
    ("WARNING", "Response time {resp_time}ms exceeds threshold 3000ms"),
    ("ERROR", "HTTP {code} on page {page}, retrying (attempt {attempt})"),
    ("INFO", "Download started: {filename} ({file_size}MB)"),
    ("DEBUG", "Parsing JSON response: keys={json_keys}"),
    ("INFO", "ETL pipeline: {etl_source} -> {etl_target} completed, {rows} rows"),
    ("WARNING", "Kafka consumer lag for {topic}: {lag} messages behind"),
    ("ERROR", "Proxy {proxy} connection timeout after {timeout}s"),
    ("INFO", "Checkpoint saved: template={tpl_name}, page={page}"),
    ("DEBUG", "Bloom filter size: {bloom_size} bits, hash functions: {funcs}"),
]


@router.websocket("/monitor/ws")
async def monitor_websocket(ws: WebSocket) -> None:
    """WebSocket 日志流端点。

    建立连接后，每 2 秒推送一条模拟日志行（JSON 格式）。
    客户端断开连接时自动停止推送。
    """
    await ws.accept()

    page = 1
    try:
        while True:
            # 构造模拟日志行
            level, template_str = random.choice(_LOG_TEMPLATES)
            log_source = random.choice(_LOG_SOURCES)
            message = template_str.format(
                page=page,
                count=random.randint(10, 200),
                req_headers="{Accept: application/json}",
                rec_id=random.randint(10000, 99999),
                resp_time=random.randint(500, 8000),
                code=random.choice([200, 403, 429, 500, 503]),
                attempt=random.randint(1, 5),
                filename=f"patent_{random.randint(1000, 9999)}.pdf",
                file_size=round(random.uniform(0.1, 15.0), 1),
                json_keys="['results', 'cluster', 'total_num_results']",
                etl_source=random.choice(["rds", "ods", "dwd"]),
                etl_target=random.choice(["ods", "dwd", "ads"]),
                rows=random.randint(100, 5000),
                topic=random.choice(["spider.rds", "spider.ods", "spider.dwd"]),
                lag=random.randint(5, 500),
                proxy=f"{random.randint(10,200)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(1,254)}:{random.randint(1080,9090)}",
                timeout=random.randint(10, 60),
                tpl_name=random.choice(["google_patent", "uspto", "wipo"]),
                bloom_size=random.randint(100000, 10000000),
                funcs=random.randint(3, 10),
            )

            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "source": log_source,
                "message": message,
            }

            await ws.send_text(json.dumps(log_entry, ensure_ascii=False))

            page += 1
            if page > 99:
                page = 1

            await asyncio.sleep(2)

    except WebSocketDisconnect:
        pass
    except asyncio.CancelledError:
        pass
