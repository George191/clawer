from __future__ import annotations

from datetime import datetime, timezone
from functools import partial
from typing import Any

from sqlalchemy import text

from app.config.settings import settings
from app.etl.base import ETLBase
from app.etl.normalizers import get_normalizer
from app.logger import get_logger

logger = get_logger(__name__)

_ODS_NEWS_TABLE = "ts_ods.ods_news"
_ODS_PATENT_TABLE = "ts_ods.ods_patent"
_ODS_NAVWARN_TABLE = "ts_ods.ods_navwarn"
_ODS_INTELLIGENCE_TABLE = "ts_ods.ods_intelligence"


def _prefer_text(table_ref: str, column: str) -> str:
    return f"COALESCE(NULLIF(BTRIM(EXCLUDED.{column}), ''), {table_ref}.{column})"


def _prefer_value(table_ref: str, column: str) -> str:
    return f"COALESCE(EXCLUDED.{column}, {table_ref}.{column})"


def _prefer_json(table_ref: str, column: str) -> str:
    return (
        f"CASE "
        f"WHEN EXCLUDED.{column} IS NULL "
        f"OR EXCLUDED.{column} = '[]'::jsonb "
        f"OR EXCLUDED.{column} = '{{}}'::jsonb "
        f"OR EXCLUDED.{column} = 'null'::jsonb "
        f"THEN {table_ref}.{column} "
        f"ELSE EXCLUDED.{column} END"
    )


def _prefer_geography(table_ref: str, column: str) -> str:
    return f"COALESCE(EXCLUDED.{column}, {table_ref}.{column})"


ODS_NEWS_INSERT = f"""
INSERT INTO ts_ods.ods_news (
    record_id, data_source, data_type,
    title, url, source_url, source_published_at, source_updated_at,
    summary, content, content_html, summary_html,
    author, news_type, organization, tags, external_links,
    attachments, images, slides, thumbnail,
    created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type,
    :title, :url, :source_url, CAST(:source_published_at AS timestamptz), CAST(:source_updated_at AS timestamptz),
    :summary, :content, :content_html, :summary_html,
    :author, CAST(:news_type AS jsonb), CAST(:organization AS jsonb), CAST(:tags AS jsonb), CAST(:external_links AS jsonb),
    CAST(:attachments AS jsonb), CAST(:images AS jsonb), CAST(:slides AS jsonb), :thumbnail,
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
    data_source = EXCLUDED.data_source,
    data_type = EXCLUDED.data_type,
    title = {_prefer_text(_ODS_NEWS_TABLE, "title")},
    url = {_prefer_text(_ODS_NEWS_TABLE, "url")},
    source_url = {_prefer_text(_ODS_NEWS_TABLE, "source_url")},
    source_published_at = {_prefer_value(_ODS_NEWS_TABLE, "source_published_at")},
    source_updated_at = {_prefer_value(_ODS_NEWS_TABLE, "source_updated_at")},
    summary = {_prefer_text(_ODS_NEWS_TABLE, "summary")},
    content = {_prefer_text(_ODS_NEWS_TABLE, "content")},
    content_html = {_prefer_text(_ODS_NEWS_TABLE, "content_html")},
    summary_html = {_prefer_text(_ODS_NEWS_TABLE, "summary_html")},
    author = {_prefer_text(_ODS_NEWS_TABLE, "author")},
    news_type = {_prefer_json(_ODS_NEWS_TABLE, "news_type")},
    organization = {_prefer_json(_ODS_NEWS_TABLE, "organization")},
    tags = {_prefer_json(_ODS_NEWS_TABLE, "tags")},
    external_links = {_prefer_json(_ODS_NEWS_TABLE, "external_links")},
    attachments = {_prefer_json(_ODS_NEWS_TABLE, "attachments")},
    images = {_prefer_json(_ODS_NEWS_TABLE, "images")},
    slides = {_prefer_json(_ODS_NEWS_TABLE, "slides")},
    thumbnail = {_prefer_text(_ODS_NEWS_TABLE, "thumbnail")},
    updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_PATENT_INSERT = f"""
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
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
    data_source = EXCLUDED.data_source,
    data_type = EXCLUDED.data_type,
    title = {_prefer_text(_ODS_PATENT_TABLE, "title")},
    publication_number = {_prefer_text(_ODS_PATENT_TABLE, "publication_number")},
    application_number = {_prefer_text(_ODS_PATENT_TABLE, "application_number")},
    assignee = {_prefer_text(_ODS_PATENT_TABLE, "assignee")},
    inventor = {_prefer_text(_ODS_PATENT_TABLE, "inventor")},
    publication_date = {_prefer_value(_ODS_PATENT_TABLE, "publication_date")},
    filing_date = {_prefer_value(_ODS_PATENT_TABLE, "filing_date")},
    priority_date = {_prefer_value(_ODS_PATENT_TABLE, "priority_date")},
    grant_date = {_prefer_value(_ODS_PATENT_TABLE, "grant_date")},
    abstract = {_prefer_text(_ODS_PATENT_TABLE, "abstract")},
    claims = {_prefer_json(_ODS_PATENT_TABLE, "claims")},
    legal_status = {_prefer_text(_ODS_PATENT_TABLE, "legal_status")},
    ipc_classification = {_prefer_text(_ODS_PATENT_TABLE, "ipc_classification")},
    cpc_classification = {_prefer_text(_ODS_PATENT_TABLE, "cpc_classification")},
    patent_type = {_prefer_text(_ODS_PATENT_TABLE, "patent_type")},
    url = {_prefer_text(_ODS_PATENT_TABLE, "url")},
    thumbnail = {_prefer_text(_ODS_PATENT_TABLE, "thumbnail")},
    figures = {_prefer_json(_ODS_PATENT_TABLE, "figures")},
    quality_score = {_prefer_value(_ODS_PATENT_TABLE, "quality_score")},
    quality_flags = {_prefer_json(_ODS_PATENT_TABLE, "quality_flags")},
    updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_NAVWARN_INSERT = f"""
INSERT INTO ts_ods.ods_navwarn (
    record_id, data_source, data_type,
    navarea_id, warning_no, serial_number, warning_year, region,
    issued_at, message_text, hazard_type, coordinate,
    quality_score, quality_flags, created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type,
    CAST(:navarea_id AS integer), :warning_no, CAST(:serial_number AS integer), CAST(:warning_year AS integer), :region,
    CAST(:issued_at AS timestamptz), :message_text, :hazard_type,
    CASE
        WHEN NULLIF(BTRIM(CAST(:coordinate AS text)), '') IS NULL THEN NULL
        ELSE ST_GeogFromText(:coordinate)
    END,
    CAST(:quality_score AS double precision), CAST(:quality_flags AS jsonb),
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
    data_source = EXCLUDED.data_source,
    data_type = EXCLUDED.data_type,
    navarea_id = {_prefer_value(_ODS_NAVWARN_TABLE, "navarea_id")},
    warning_no = {_prefer_text(_ODS_NAVWARN_TABLE, "warning_no")},
    serial_number = {_prefer_value(_ODS_NAVWARN_TABLE, "serial_number")},
    warning_year = {_prefer_value(_ODS_NAVWARN_TABLE, "warning_year")},
    region = {_prefer_text(_ODS_NAVWARN_TABLE, "region")},
    issued_at = {_prefer_value(_ODS_NAVWARN_TABLE, "issued_at")},
    message_text = {_prefer_text(_ODS_NAVWARN_TABLE, "message_text")},
    hazard_type = {_prefer_text(_ODS_NAVWARN_TABLE, "hazard_type")},
    coordinate = {_prefer_geography(_ODS_NAVWARN_TABLE, "coordinate")},
    quality_score = {_prefer_value(_ODS_NAVWARN_TABLE, "quality_score")},
    quality_flags = {_prefer_json(_ODS_NAVWARN_TABLE, "quality_flags")},
    updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_INTELLIGENCE_INSERT = f"""
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
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
    data_source = EXCLUDED.data_source,
    data_type = EXCLUDED.data_type,
    title = {_prefer_text(_ODS_INTELLIGENCE_TABLE, "title")},
    url = {_prefer_text(_ODS_INTELLIGENCE_TABLE, "url")},
    source_published_at = {_prefer_value(_ODS_INTELLIGENCE_TABLE, "source_published_at")},
    source_updated_at = {_prefer_value(_ODS_INTELLIGENCE_TABLE, "source_updated_at")},
    summary = {_prefer_text(_ODS_INTELLIGENCE_TABLE, "summary")},
    file_name = {_prefer_text(_ODS_INTELLIGENCE_TABLE, "file_name")},
    file_size = {_prefer_text(_ODS_INTELLIGENCE_TABLE, "file_size")},
    file_type = {_prefer_text(_ODS_INTELLIGENCE_TABLE, "file_type")},
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

    async def _write_current(
        self,
        *,
        table: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        current_sql = _ODS_INSERT_SQL[table]

        async with self._pg.session() as session:
            result = await session.execute(text(current_sql), payload)
            current_row = result.mappings().first()
            return dict(current_row) if current_row else None

    def _validate_required_fields(
        self,
        *,
        table: str,
        data_source: str,
        normalized: dict[str, Any],
    ) -> bool:
        if table == "news" and normalized.get("source_published_at") is None:
            logger.warning(
                "%s Normalized news missing source_published_at, source=%s record_id=%s",
                self._log_prefix,
                data_source,
                normalized.get("record_id"),
            )
            return False
        return True

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
            if not self._validate_required_fields(
                table=table,
                data_source=normalized.get("data_source") or data_source,
                normalized={
                    **normalized,
                    "record_id": normalized_record_id,
                },
            ):
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
                partial(self._write_current, table=table, payload=payload),
                payload=payload,
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
