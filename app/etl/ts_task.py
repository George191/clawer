"""ETL TASK 层 — 任务处理层。

设计原则：
- TASK 只存储处理结果，不重复存储上游（ODS）的源数据字段
- 每类数据一张表，字段仅含：来源、任务、结果、结果类型、记录标识、时间
- 消费方需要完整信息时 JOIN ts_ods 即可
- 调度器只做：取任务 → 调 execute() → 写 DB → emit，不接触任务依赖

架构：
    调度者 (TsTask handler) → TaskRunner → 任务器 (PdfToMarkdownTask) → 功能函数 (_download/_convert)
    MinIO 等依赖由任务器自行管理，调度器不感知

任务目录 app/etl/tasks/：
    - pdf_to_markdown   PDF 下载 + 转换 + 上传 MinIO，返回 minio 相对路径
    - satellite_vulnerability_extract   卫星漏洞抽取，返回命中关键词 JSON 数组

扩展：
    - 新增任务：tasks/ 下新建模块 → register_task → handler 中 get_tasks("task1", "task2")
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import partial
from typing import Any

from app.config.settings import settings
from app.etl.base import ETLBase, extract_meta
from app.etl.schema.registry import get_registered_ddl, save_registered_ddl
from app.etl.tasks import get_tasks
from app.logger import get_logger

logger = get_logger(__name__)

TASK_VULNERABILITY_DDL = """
CREATE TABLE IF NOT EXISTS ts_task.task_vulnerability (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    task TEXT NOT NULL,
    result JSONB NOT NULL CHECK (jsonb_typeof(result) = 'array'),
    result_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, task)
);
"""

TASK_VULNERABILITY_INSERT = """
INSERT INTO ts_task.task_vulnerability (
    data_source, data_type, record_id,
    task, result, result_type, created_at, updated_at
) VALUES (
    :data_source, :data_type, :record_id,
    :task, CAST(:result AS jsonb), :result_type,
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, task) DO UPDATE SET
    result = EXCLUDED.result,
    result_type = EXCLUDED.result_type,
    updated_at = EXCLUDED.updated_at
RETURNING *
"""

TASK_PATENT_INSERT = """
INSERT INTO ts_task.task_patent (
    data_source, data_type, record_id,
    task, result, result_type,
    created_at
) VALUES (
    :data_source, :data_type, :record_id,
    :task, :result, :result_type,
    CAST(:created_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, task, created_at) DO UPDATE SET
    result = EXCLUDED.result,
    result_type = EXCLUDED.result_type
RETURNING *
"""


class TsTask(ETLBase):
    _layer = "task"
    _consumer_topics = [settings.etl_ods_topic]
    _consumer_group = settings.etl_task_consumer_group
    _producer_topic = settings.etl_task_topic
    _producer_client_id = "etl-ts-task-producer"

    async def _dispatch(self, message: dict[str, Any]) -> bool:
        data_type = extract_meta(message).get("data_type") or message.get("data_type")
        if data_type and data_type not in self._handlers:
            return True
        return await super()._dispatch(message)

    async def _on_init_schema(self) -> None:
        if "vulnerability" in self._handlers and not await get_registered_ddl(
            self._pg, "task", "vulnerability",
        ):
            await save_registered_ddl(
                self._pg,
                layer="task",
                table="vulnerability",
                ddl_sql=TASK_VULNERABILITY_DDL,
                partition_type="none",
                description="Satellite vulnerability extraction results",
                updated_by="system:task",
                allow_update=False,
            )
        await super()._on_init_schema()

    async def _handler_vulnerability(self, message: dict[str, Any]) -> bool:
        try:
            data_source = message.get("data_source")
            record_id = message.get("record_id")
            if not data_source or not record_id:
                raise ValueError("Vulnerability task requires record_id and data_source")

            results = await get_tasks("satellite_vulnerability_extract").execute(message=message)
            now = datetime.now(timezone.utc)
            for task_name, task_result in results.items():
                if not json.loads(task_result.data):
                    continue
                payload = {
                    "data_source": data_source,
                    "data_type": "vulnerability",
                    "record_id": record_id,
                    "task": task_name,
                    "result": task_result.data,
                    "result_type": task_result.data_type,
                    "created_at": now,
                    "updated_at": now,
                }
                result = await self._execute_with_table_recovery(
                    "vulnerability",
                    partial(self._pg.fetch_one, TASK_VULNERABILITY_INSERT, payload),
                    payload=payload,
                )
                await self._emit(
                    result, record_id=record_id, data_source=data_source, data_type="vulnerability",
                )
            return True
        except Exception:
            logger.exception("%s Failed to match vulnerability keywords", self._log_prefix)
            return False

    async def _handler_patent(self, message: dict[str, Any]) -> bool:
        try:
            data_source = message.get("data_source", "")
            data_type = message.get("data_type", "")
            record_id = message.get("record_id", "")

            tasks = get_tasks("pdf_to_markdown")
            results = await tasks.execute(message=message)

            now = datetime.now(timezone.utc)
            for task_name, task_result in results.items():

                result = await self._pg.fetch_one(
                    TASK_PATENT_INSERT,
                    {
                        "data_source": data_source,
                        "data_type": data_type,
                        "record_id": record_id,
                        "task": task_name,
                        "result": task_result.data,
                        "result_type": task_result.data_type,
                        "created_at": now,
                    },
                )

                await self._emit(result, record_id=record_id, data_source=data_source, data_type=data_type)
                logger.debug(
                    "%s task=%s result_type=%s record_id=%s → ADS",
                    self._log_prefix, task_name, task_result.data_type, record_id,
                )

            return True

        except Exception:
            logger.exception("%s Failed to process task message", self._log_prefix)
            return False
