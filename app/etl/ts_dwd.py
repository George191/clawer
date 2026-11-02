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
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from app.config.settings import settings
from app.etl.base import ETLBase
from app.etl.source_fences import current_ods_revision, source_snapshot_current, source_snapshot_revision
from app.logger import get_logger

logger = get_logger(__name__)

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
    updated_at = CAST(:updated_at AS timestamptz),
    ods_revision = :source_revision,
    task_results = CASE WHEN task_revision=:source_revision THEN task_results ELSE NULL END,
    task_revision = CASE WHEN task_revision=:source_revision THEN task_revision ELSE NULL END
WHERE record_id = :record_id AND data_source = :data_source
RETURNING *
"""

DWD_PATENT_UPDATE_TASK = """
UPDATE ts_dwd.dwd_patent SET
    task_results = CAST(:task_results AS jsonb),
    task_revision = :source_revision,
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
    created_at, updated_at, ods_revision
) VALUES (
    :data_source, :data_type, :record_id,
    :title, :publication_number, :application_number, :assignee, :inventor,
    CAST(:publication_date AS date), CAST(:filing_date AS date),
    CAST(:priority_date AS date), CAST(:grant_date AS date),
    :abstract, CAST(:claims AS jsonb), :legal_status,
    :ipc_classification, :cpc_classification, :patent_type,
    :original_file, :thumbnail, CAST(:figures AS jsonb),
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz), :source_revision
)
RETURNING *
"""

DWD_PATENT_INSERT_TASK = """
INSERT INTO ts_dwd.dwd_patent (
    data_source, data_type, record_id,
    task_results,
    created_at, updated_at, task_revision
) VALUES (
    :data_source, :data_type, :record_id,
    CAST(:task_results AS jsonb),
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz), :source_revision
)
RETURNING *
"""

DWD_EXISTS = """
SELECT 1 FROM ts_dwd.dwd_patent
WHERE record_id = :record_id AND data_source = :data_source
LIMIT 1
"""

def graph_record_complete(row: dict[str, Any]) -> bool:
    """Check graph eligibility from the exact persisted row, without a newer read."""
    return (row.get("title") is not None and row.get("task_results") is not None
            and bool(row.get("ods_revision")) and row["ods_revision"] == row.get("task_revision"))


class TsDwd(ETLBase):
    _layer = "dwd"
    _consumer_topics = [settings.etl_ods_topic, settings.etl_task_topic]
    _consumer_group = settings.etl_dwd_consumer_group
    _producer_topic = settings.etl_graph_topic
    _producer_client_id = "etl-ts-dwd-producer"

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
    async def _emit_if_complete(
        self, result: dict[str, Any] | None,
        record_id: str, data_source: str, data_type: str,
    ) -> bool:
        """仅在记录完整时 emit 到 Graph Topic。"""
        if result is None:
            logger.debug("%s record_id=%s: no result, skip emit", self._log_prefix, record_id)
            return False

        is_complete = graph_record_complete(result)
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
            "original_file": message.get("url") or message.get("original_file"),
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
    #  Handler: patent
    # =================================================================
    async def _handler_patent(self, message: dict[str, Any]) -> bool:
        try:
            data_source = message.get("data_source", "")
            data_type = message.get("data_type", "")
            record_id = message.get("record_id", "")

            kafka_meta = message.get("_kafka_meta", {})
            kafka_topic = kafka_meta.get("kafka_topic", "")
            if kafka_topic not in {settings.etl_ods_topic, settings.etl_task_topic}:
                raise ValueError("DWD requires a known ODS or TASK input topic")
            is_ods_source = kafka_topic == settings.etl_ods_topic

            now = datetime.now(timezone.utc)
            lock_key = "dwd:" + json.dumps([record_id, data_source, data_type])
            async with self._pg.locked_transaction(lock_key) as session:
                if is_ods_source:
                    revision = await source_snapshot_revision(session, "ods", message)
                else:
                    revision = message.get("source_revision")
                    if not revision or revision != await current_ods_revision(session, message):
                        return True
                    if not await source_snapshot_current(session, "task", message):
                        return True
                if revision is None:
                    return True
                result, is_new = await self._merge_patent(session, message, is_ods_source, now, revision)

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

    async def _merge_patent(self, session, message, is_ods_source, now, revision):
        """Merge one verified upstream snapshot while holding the DWD identity lock."""
        identity = {name: message[name] for name in ("record_id", "data_source", "data_type")}
        existing = (await session.execute(text(DWD_EXISTS), identity)).mappings().first()
        params = {**identity, "updated_at": now, "source_revision": revision}
        if existing is None:
            params["created_at"] = now
        if is_ods_source:
            params.update(self._build_patent_params(message))
            sql = DWD_PATENT_INSERT_ODS if existing is None else DWD_PATENT_UPDATE_ODS
        else:
            params["task_results"] = self._build_task_results(message)
            sql = DWD_PATENT_INSERT_TASK if existing is None else DWD_PATENT_UPDATE_TASK
        row = (await session.execute(text(sql), params)).mappings().first()
        return dict(row) if row else None, existing is None
