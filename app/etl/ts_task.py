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

扩展：
    - 新增任务：tasks/ 下新建模块 → register_task → handler 中 get_tasks("task1", "task2")
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.etl.base import ETLBase
from app.etl.tasks import get_tasks

logger = logging.getLogger(__name__)

TASK_PATENT_DDL = """
CREATE TABLE IF NOT EXISTS ts_task.task_patent (
    id              BIGSERIAL,
    data_source     VARCHAR(128)    NOT NULL,
    data_type       VARCHAR(64)     NOT NULL,
    record_id       VARCHAR(256)    NOT NULL,

    task            VARCHAR(64)     NOT NULL,
    result          TEXT,
    result_type     VARCHAR(32)     NOT NULL,

    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_task_patent PRIMARY KEY (id, created_at),
    CONSTRAINT uq_task_result UNIQUE (record_id, data_source, task, created_at)
) PARTITION BY RANGE (created_at);
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

    async def _ddl_for_table(self, table: str) -> str:
        ddl_map = {
            "patent": TASK_PATENT_DDL,
        }
        ddl = ddl_map.get(table, "")
        if not ddl:
            logger.warning("%s No DDL defined for table '%s'", self._log_prefix, table)
        return ddl

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