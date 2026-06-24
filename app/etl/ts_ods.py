from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.config.settings import settings
from app.etl.base import ETLBase
from app.etl.normalizers import get_normalizer

logger = logging.getLogger(__name__)

ODS_NEWS_INSERT = """
INSERT INTO ts_ods.ods_news (
    record_id, data_source, data_type,
    title, url, source_url, source_published_at, source_updated_at,
    summary, content, content_html, summary_html,
    author, organization, tags, external_links,
    attachments, images, slides, thumbnail,
    created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type,
    :title, :url, :source_url, CAST(:source_published_at AS timestamptz), CAST(:source_updated_at AS timestamptz),
    :summary, :content, :content_html, :summary_html,
    :author, CAST(:organization AS jsonb), CAST(:tags AS jsonb), CAST(:external_links AS jsonb),
    CAST(:attachments AS jsonb), CAST(:images AS jsonb), CAST(:slides AS jsonb), :thumbnail,
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id) DO UPDATE SET
    data_source = EXCLUDED.data_source,
    data_type = EXCLUDED.data_type,
    title = EXCLUDED.title,
    url = EXCLUDED.url,
    source_url = EXCLUDED.source_url,
    source_published_at = EXCLUDED.source_published_at,
    source_updated_at = EXCLUDED.source_updated_at,
    summary = EXCLUDED.summary,
    content = EXCLUDED.content,
    content_html = EXCLUDED.content_html,
    summary_html = EXCLUDED.summary_html,
    author = EXCLUDED.author,
    organization = EXCLUDED.organization,
    tags = EXCLUDED.tags,
    external_links = EXCLUDED.external_links,
    attachments = EXCLUDED.attachments,
    images = EXCLUDED.images,
    slides = EXCLUDED.slides,
    thumbnail = EXCLUDED.thumbnail,
    updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_PATENT_INSERT = """
INSERT INTO ts_ods.ods_patent (
    record_id, data_source, data_type,
    title, publication_number, application_number, assignee, inventor,
    publication_date, filing_date, priority_date, grant_date,
    abstract, claims, legal_status, ipc_classification, cpc_classification, patent_type,
    url, thumbnail, figures, quality_score, quality_flags,
    created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type,
    :title, :publication_number, :application_number, :assignee, :inventor,
    CAST(:publication_date AS date), CAST(:filing_date AS date), CAST(:priority_date AS date), CAST(:grant_date AS date),
    :abstract, CAST(:claims AS jsonb), :legal_status, :ipc_classification, :cpc_classification, :patent_type,
    :url, :thumbnail, CAST(:figures AS jsonb), CAST(:quality_score AS double precision), CAST(:quality_flags AS jsonb),
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id) DO UPDATE SET
    data_source = EXCLUDED.data_source,
    data_type = EXCLUDED.data_type,
    title = EXCLUDED.title,
    publication_number = EXCLUDED.publication_number,
    application_number = EXCLUDED.application_number,
    assignee = EXCLUDED.assignee,
    inventor = EXCLUDED.inventor,
    publication_date = EXCLUDED.publication_date,
    filing_date = EXCLUDED.filing_date,
    priority_date = EXCLUDED.priority_date,
    grant_date = EXCLUDED.grant_date,
    abstract = EXCLUDED.abstract,
    claims = EXCLUDED.claims,
    legal_status = EXCLUDED.legal_status,
    ipc_classification = EXCLUDED.ipc_classification,
    cpc_classification = EXCLUDED.cpc_classification,
    patent_type = EXCLUDED.patent_type,
    url = EXCLUDED.url,
    thumbnail = EXCLUDED.thumbnail,
    figures = EXCLUDED.figures,
    quality_score = EXCLUDED.quality_score,
    quality_flags = EXCLUDED.quality_flags,
    updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_NAVWARN_INSERT = """
INSERT INTO ts_ods.ods_navwarn (
    record_id, data_source, data_type,
    navarea_id, warning_no, warning_prefix, serial_number, warning_year, sea_name,
    issued_at, message_text, hazard_type, coordinates,
    quality_score, quality_flags, created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type,
    CAST(:navarea_id AS integer), :warning_no, :warning_prefix, CAST(:serial_number AS integer), CAST(:warning_year AS integer), :sea_name,
    CAST(:issued_at AS timestamptz), :message_text, :hazard_type, CAST(:coordinates AS jsonb),
    CAST(:quality_score AS double precision), CAST(:quality_flags AS jsonb),
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id) DO UPDATE SET
    data_source = EXCLUDED.data_source,
    data_type = EXCLUDED.data_type,
    navarea_id = EXCLUDED.navarea_id,
    warning_no = EXCLUDED.warning_no,
    warning_prefix = EXCLUDED.warning_prefix,
    serial_number = EXCLUDED.serial_number,
    warning_year = EXCLUDED.warning_year,
    sea_name = EXCLUDED.sea_name,
    issued_at = EXCLUDED.issued_at,
    message_text = EXCLUDED.message_text,
    hazard_type = EXCLUDED.hazard_type,
    coordinates = EXCLUDED.coordinates,
    quality_score = EXCLUDED.quality_score,
    quality_flags = EXCLUDED.quality_flags,
    updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_INTELLIGENCE_INSERT = """
INSERT INTO ts_ods.ods_intelligence (
    record_id, data_source, data_type,
    title, url, source_published_at, source_updated_at, summary,
    file_name, file_size, file_type,
    created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type,
    :title, :url, CAST(:source_published_at AS timestamptz), CAST(:source_updated_at AS timestamptz), :summary,
    :file_name, :file_size, :file_type,
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id) DO UPDATE SET
    data_source = EXCLUDED.data_source,
    data_type = EXCLUDED.data_type,
    title = EXCLUDED.title,
    url = EXCLUDED.url,
    source_published_at = EXCLUDED.source_published_at,
    source_updated_at = EXCLUDED.source_updated_at,
    summary = EXCLUDED.summary,
    file_name = EXCLUDED.file_name,
    file_size = EXCLUDED.file_size,
    file_type = EXCLUDED.file_type,
    updated_at = EXCLUDED.updated_at
RETURNING *
"""

_ODS_INSERT_SQL = {
    "news": ODS_NEWS_INSERT,
    "patent": ODS_PATENT_INSERT,
    "navwarn": ODS_NAVWARN_INSERT,
    "intelligence": ODS_INTELLIGENCE_INSERT,
}


class TsOds(ETLBase):
    _layer = "ods"
    _consumer_topics = [settings.etl_rds_topic]
    _consumer_group = settings.etl_ods_consumer_group
    _producer_topic = settings.etl_ods_topic
    _producer_client_id = "etl-ts-ods-producer"

    async def _handler_news(self, message: dict[str, Any]) -> bool:
        return await self._process_ods_record(message, table="news")

    async def _handler_patent(self, message: dict[str, Any]) -> bool:
        return await self._process_ods_record(message, table="patent")

    async def _handler_navwarn(self, message: dict[str, Any]) -> bool:
        return await self._process_ods_record(message, table="navwarn")

    async def _handler_intelligence(self, message: dict[str, Any]) -> bool:
        return await self._process_ods_record(message, table="intelligence")

    async def _process_ods_record(self, message: dict[str, Any], table: str) -> bool:
        insert_sql = _ODS_INSERT_SQL.get(table)
        if not insert_sql:
            logger.warning("%s Unsupported ODS table: %s", self._log_prefix, table)
            return False

        try:
            data_type = message.get("data_type", "") or table
            data_source = message.get("data_source", "")
            record_id = message.get("record_id", "")

            raw_data = message.get("raw_data", message)
            normalizer = get_normalizer(data_type, data_source)
            normalized = normalizer(raw_data)

            normalized_record_id = normalized.get("record_id") or record_id
            if not normalized_record_id:
                logger.warning("%s Normalized message missing record_id, table=%s", self._log_prefix, table)
                return False

            now = datetime.now(timezone.utc)
            payload = {
                **normalized,
                "record_id": normalized_record_id,
                "data_source": normalized.get("data_source") or data_source,
                "data_type": normalized.get("data_type") or data_type,
                "created_at": now,
                "updated_at": now,
            }
            result = await self._execute_with_table_recovery(
                table,
                lambda: self._pg.fetch_one(insert_sql, payload),
            )

            await self._emit(
                result,
                record_id=normalized_record_id,
                data_source=result.get("data_source") if result else normalized.get("data_source"),
                data_type=result.get("data_type") if result else normalized.get("data_type"),
            )
            logger.debug(
                "%s Normalized table=ods_%s record_id=%s source=%s",
                self._log_prefix,
                table,
                normalized_record_id,
                normalized.get("data_source"),
            )
            return True
        except Exception:
            logger.exception("%s Failed to normalize table=%s", self._log_prefix, table)
            return False
