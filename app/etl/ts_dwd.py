"""ETL DWD 层 — 数据明细层（Data Warehouse Detail）。

职责：
- 双源消费：从 Kafka 同时消费 ODS 标准化数据 + TASK 算法结果
- 宽表合并：将 ODS 专利字段与 TASK 算法结果合并为一张业务宽表
- 图谱推送：构建图谱结构数据，推送到 Graph Topic

架构：
    ODS Topic ──┐
                ├──→ DWD ──→ ts_dwd.dwd_patent (宽表)
    TASK Topic ─┘              │
                               └──→ Graph Topic (图谱)

消息路由与完整度门控：
    _kafka_meta.kafka_topic → 识别来源（ODS / TASK）
    → check-exists: 对方是否已入库？
        ├── 已入库 → UPSERT 合并 → 检查完整度 → 完整则 emit Graph
        └── 未入库 → INSERT 部分记录 → 等待对方到达 → 对方到达后合并 + emit
    
    时序 A: ODS 先到 → INSERT (task_results=null) → ⏳不emit
            TASK 后到 → UPDATE task_results → ✅完整 → emit
    时序 B: TASK 先到 → INSERT 最小记录 → ⏳不emit
            ODS 后到 → UPDATE 专利字段 → ✅完整 → emit
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.etl.base import ETLBase

logger = logging.getLogger(__name__)

DWD_PATENT_DDL = """
CREATE TABLE IF NOT EXISTS ts_dwd.dwd_patent (
    id                  BIGSERIAL,
    data_source         VARCHAR(128)    NOT NULL,
    data_type           VARCHAR(64)     NOT NULL,
    record_id           VARCHAR(256)    NOT NULL,

    title               TEXT,
    publication_number  VARCHAR(128),
    application_number  VARCHAR(128),
    assignee            TEXT,
    inventor            TEXT,
    publication_date    DATE,
    filing_date         DATE,
    priority_date       DATE,
    grant_date          DATE,
    abstract            TEXT,
    claims              JSONB,
    legal_status        VARCHAR(64),
    ipc_classification  VARCHAR(256),
    cpc_classification  VARCHAR(256),
    patent_type         VARCHAR(64),
    original_file       TEXT,
    thumbnail           TEXT,
    figures             JSONB,

    task_results        JSONB,

    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    CONSTRAINT pk_dwd_patent PRIMARY KEY (id, created_at),
    CONSTRAINT uq_dwd_record UNIQUE (record_id, data_source, created_at)
) PARTITION BY RANGE (created_at);
"""

DWD_PATENT_UPDATE_ODS = """
UPDATE ts_dwd.dwd_patent SET
    title = :title,
    publication_number = :publication_number,
    application_number = :application_number,
    assignee = :assignee,
    inventor = :inventor,
    publication_date = CAST(:publication_date AS date),
    filing_date = CAST(:filing_date AS date),
    priority_date = CAST(:priority_date AS date),
    grant_date = CAST(:grant_date AS date),
    abstract = :abstract,
    claims = CAST(:claims AS jsonb),
    legal_status = :legal_status,
    ipc_classification = :ipc_classification,
    cpc_classification = :cpc_classification,
    patent_type = :patent_type,
    original_file = :original_file,
    thumbnail = :thumbnail,
    figures = CAST(:figures AS jsonb),
    updated_at = CAST(:updated_at AS timestamptz)
WHERE record_id = :record_id AND data_source = :data_source
RETURNING *
"""

DWD_PATENT_UPDATE_TASK = """
UPDATE ts_dwd.dwd_patent SET
    task_results = CAST(:task_results AS jsonb),
    updated_at = CAST(:updated_at AS timestamptz)
WHERE record_id = :record_id AND data_source = :data_source
RETURNING *
"""

DWD_PATENT_INSERT_ODS = """
INSERT INTO ts_dwd.dwd_patent (
    data_source, data_type, record_id,
    title, publication_number, application_number, assignee, inventor,
    publication_date, filing_date, priority_date, grant_date,
    abstract, claims, legal_status,
    ipc_classification, cpc_classification, patent_type,
    original_file, thumbnail, figures,
    created_at, updated_at
) VALUES (
    :data_source, :data_type, :record_id,
    :title, :publication_number, :application_number, :assignee, :inventor,
    CAST(:publication_date AS date), CAST(:filing_date AS date),
    CAST(:priority_date AS date), CAST(:grant_date AS date),
    :abstract, CAST(:claims AS jsonb), :legal_status,
    :ipc_classification, :cpc_classification, :patent_type,
    :original_file, :thumbnail, CAST(:figures AS jsonb),
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
RETURNING *
"""

DWD_PATENT_INSERT_TASK = """
INSERT INTO ts_dwd.dwd_patent (
    data_source, data_type, record_id,
    task_results,
    created_at, updated_at
) VALUES (
    :data_source, :data_type, :record_id,
    CAST(:task_results AS jsonb),
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
RETURNING *
"""

DWD_EXISTS = """
SELECT 1 FROM ts_dwd.dwd_patent
WHERE record_id = :record_id AND data_source = :data_source
LIMIT 1
"""

# 完整度检查：只有 ODS 专利字段 + TASK 分析结果都就位，记录才算完整
DWD_COMPLETENESS_CHECK = """
SELECT title IS NOT NULL AND task_results IS NOT NULL AS is_complete
FROM ts_dwd.dwd_patent
WHERE record_id = :record_id AND data_source = :data_source
LIMIT 1
"""


class TsDwd(ETLBase):
    _layer = "dwd"
    _consumer_topics = [settings.etl_ods_topic, settings.etl_task_topic]
    _consumer_group = settings.etl_dwd_consumer_group
    _producer_topic = settings.etl_graph_topic
    _producer_client_id = "etl-ts-dwd-producer"
    
    async def _ddl_for_table(self, table: str) -> str:
        ddl_map = {
            "patent": DWD_PATENT_DDL,
        }
        ddl = ddl_map.get(table, "")
        if not ddl:
            logger.warning("%s No DDL defined for table '%s'", self._log_prefix, table)
        return ddl

    # =================================================================
    #  公共参数构建 — 消除重复
    # =================================================================
    @staticmethod
    def _parse_date(date_str):
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    # =================================================================
    #  完整度门控 — 只有双方数据都就位才 emit 到 Graph
    # =================================================================
    async def _is_record_complete(self, record_id: str, data_source: str) -> bool:
        """检查 ODS 专利字段 + TASK 分析结果是否都已到位。

        ODS 先到 → title 有值，task_results 为空 → 不完整
        TASK 先到 → task_results 有值，title 为空 → 不完整
        双方都到 → 完整 → 可以 emit
        """
        row = await self._pg.fetch_one(
            DWD_COMPLETENESS_CHECK,
            {"record_id": record_id, "data_source": data_source},
        )
        return bool(row and row.get("is_complete"))

    async def _emit_if_complete(
        self, result: dict[str, Any] | None,
        record_id: str, data_source: str, data_type: str,
    ) -> bool:
        """仅在记录完整时 emit 到 Graph Topic。"""
        if result is None:
            logger.debug("%s record_id=%s: no result, skip emit", self._log_prefix, record_id)
            return False

        is_complete = await self._is_record_complete(record_id, data_source)
        if is_complete:
            await self._emit(
                result, record_id=record_id,
                data_source=data_source, data_type=data_type,
            )
            logger.info(
                "%s ✅ record_id=%s complete → emit to graph",
                self._log_prefix, record_id,
            )
            return True
        else:
            logger.info(
                "%s ⏳ record_id=%s partial → skip emit (waiting for counterpart)",
                self._log_prefix, record_id,
            )
            return False

    @staticmethod
    def _build_patent_params(message: dict[str, Any]) -> dict[str, Any]:
        """从消息中提取并标准化专利字段，返回参数字典。"""
        return {
            "title": message.get("title"),
            "publication_number": message.get("publication_number"),
            "application_number": message.get("application_number"),
            "assignee": message.get("assignee"),
            "inventor": message.get("inventor"),
            "publication_date": TsDwd._parse_date(message.get("publication_date")),
            "filing_date": TsDwd._parse_date(message.get("filing_date")),
            "priority_date": TsDwd._parse_date(message.get("priority_date")),
            "grant_date": TsDwd._parse_date(message.get("grant_date")),
            "abstract": message.get("abstract"),
            "claims": json.dumps(message.get("claims") or {}),
            "legal_status": message.get("legal_status"),
            "ipc_classification": message.get("ipc_classification"),
            "cpc_classification": message.get("cpc_classification"),
            "patent_type": message.get("patent_type"),
            "original_file": message.get("original_file"),
            "thumbnail": message.get("thumbnail"),
            "figures": json.dumps(message.get("figures") or {}),
        }

    @staticmethod
    def _build_task_results(message: dict[str, Any]) -> str | None:
        """从 TASK 源消息中提取 task/result/result_type，构建 task_results JSON。"""
        task_name = message.get("task", "")
        result_data = message.get("result")
        result_type = message.get("result_type", "")
        if not task_name or result_data is None:
            return None
        return json.dumps({task_name: {"result": result_data, "result_type": result_type}})

    # =================================================================
    #  Handler: patent — 兜底（无 topic hint 或未知 topic）
    # =================================================================
    async def _handler_patent(self, message: dict[str, Any]) -> bool:
        try:
            data_source = message.get("data_source", "")
            data_type = message.get("data_type", "")
            record_id = message.get("record_id", "")

            kafka_meta = message.get("_kafka_meta", {})
            kafka_topic = kafka_meta.get("kafka_topic", "")
            is_ods_source = kafka_topic == settings.etl_ods_topic

            now = datetime.now(timezone.utc)

            existing = await self._pg.fetch_one(DWD_EXISTS, {
                "record_id": record_id,
                "data_source": data_source,
            })

            if existing is None:
                if is_ods_source:
                    params = self._build_patent_params(message)
                    params.update({
                        "data_source": data_source,
                        "data_type": data_type,
                        "record_id": record_id,
                        "created_at": now,
                        "updated_at": now,
                    })
                    result = await self._pg.fetch_one(DWD_PATENT_INSERT_ODS, params)
                else:
                    task_results = self._build_task_results(message)
                    result = await self._pg.fetch_one(DWD_PATENT_INSERT_TASK, {
                        "data_source": data_source,
                        "data_type": data_type,
                        "record_id": record_id,
                        "task_results": task_results,
                        "created_at": now,
                        "updated_at": now,
                    })
                is_new = True
            elif is_ods_source:
                params = self._build_patent_params(message)
                params.update({
                    "record_id": record_id,
                    "data_source": data_source,
                    "updated_at": now,
                })
                result = await self._pg.fetch_one(DWD_PATENT_UPDATE_ODS, params)
                is_new = False
            else:
                task_results = self._build_task_results(message)
                params = {
                    "record_id": record_id,
                    "data_source": data_source,
                    "task_results": task_results,
                    "updated_at": now,
                }
                result = await self._pg.fetch_one(DWD_PATENT_UPDATE_TASK, params)
                is_new = False

            emitted = await self._emit_if_complete(
                result, record_id=record_id,
                data_source=data_source, data_type=data_type,
            )

            logger.info(
                "%s %s patent record_id=%s source=%s topic=%s%s",
                self._log_prefix,
                "INSERT" if is_new else "UPDATE",
                record_id,
                data_source,
                "ods" if is_ods_source else "task",
                " -> graph" if emitted else " (skipped, incomplete)",
            )
            return True

        except Exception:
            logger.exception("%s Failed to merge patent (fallback)", self._log_prefix)
            return False
