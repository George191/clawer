from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from functools import partial
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError

from app.config.settings import settings
from app.etl.base import ETLBase
from app.etl.normalizers import get_normalizer
from app.etl.normalizers.twitter import normalize_twitter as _normalize_twitter  # noqa: F401
from app.logger import get_logger
from app.quality.validator import create_navwarn_validation_engine

logger = get_logger(__name__)

_ODS_NEWS_TABLE = "ts_ods.ods_news"
_ODS_PATENT_TABLE = "ts_ods.ods_patent"
_ODS_NAVWARN_TABLE = "ts_ods.ods_navwarn"
_ODS_INTELLIGENCE_TABLE = "ts_ods.ods_intelligence"
_ODS_COMPANY_TABLE = "ts_ods.ods_company"
_ODS_FILING_TABLE = "ts_ods.ods_filing"
_ODS_FILING_DOCUMENT_TABLE = "ts_ods.ods_filing_document"

# ODS 单条入库重试配置：仅针对可重试的连接/瞬时错误
_ODS_WRITE_MAX_ATTEMPTS = 3
_ODS_WRITE_RETRY_WAIT = 0.5


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
    attachments, images, slides, thumbnail, videos, iframes, audios,
    created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type,
    :title, :url, :source_url, CAST(:source_published_at AS timestamptz), CAST(:source_updated_at AS timestamptz),
    :summary, :content, :content_html, :summary_html,
    :author, CAST(:news_type AS jsonb), CAST(:organization AS jsonb), CAST(:tags AS jsonb), CAST(:external_links AS jsonb),
    CAST(:attachments AS jsonb), CAST(:images AS jsonb), CAST(:slides AS jsonb), :thumbnail,
    CAST(:videos AS jsonb), CAST(:iframes AS jsonb), CAST(:audios AS jsonb),
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
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
    news_type = EXCLUDED.news_type,
    organization = EXCLUDED.organization,
    tags = EXCLUDED.tags,
    external_links = EXCLUDED.external_links,
    attachments = EXCLUDED.attachments,
    images = EXCLUDED.images,
    slides = EXCLUDED.slides,
    thumbnail = EXCLUDED.thumbnail,
    videos = EXCLUDED.videos,
    iframes = EXCLUDED.iframes,
    audios = EXCLUDED.audios,
    updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_PATENT_INSERT = f"""
INSERT INTO ts_ods.ods_patent (
    record_id, data_source, data_type,
    title, publication_number, application_number, assignee, inventor,
    publication_date, filing_date, priority_date, grant_date,
    abstract, claims, legal_status, ipc_classification, cpc_classification, patent_type,
    url, thumbnail, figures,
    created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type,
    :title, :publication_number, :application_number, :assignee, :inventor,
    CAST(:publication_date AS date), CAST(:filing_date AS date), CAST(:priority_date AS date), CAST(:grant_date AS date),
    :abstract, CAST(:claims AS jsonb), :legal_status, :ipc_classification, :cpc_classification, :patent_type,
    :url, :thumbnail, CAST(:figures AS jsonb),
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
    updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_NAVWARN_INSERT = f"""
INSERT INTO ts_ods.ods_navwarn (
    record_id, data_source, data_type,
    navarea_id, warning_no, serial_number, warning_year, region,
    issued_at, message_text, category, status, sub_region, oceans, dnc_region, coordinate,
    created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type,
    CAST(:navarea_id AS integer), :warning_no, CAST(:serial_number AS integer), CAST(:warning_year AS integer), :region,
    CAST(:issued_at AS timestamptz), :message_text, :category, :status, :sub_region, :oceans, :dnc_region,
    CASE
        WHEN NULLIF(BTRIM(CAST(:coordinate AS text)), '') IS NULL THEN NULL
        ELSE ST_GeogFromText(:coordinate)
    END,
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
    category = {_prefer_text(_ODS_NAVWARN_TABLE, "category")},
    status = {_prefer_text(_ODS_NAVWARN_TABLE, "status")},
    sub_region = {_prefer_text(_ODS_NAVWARN_TABLE, "sub_region")},
    oceans = {_prefer_text(_ODS_NAVWARN_TABLE, "oceans")},
    dnc_region = {_prefer_text(_ODS_NAVWARN_TABLE, "dnc_region")},
    coordinate = {_prefer_geography(_ODS_NAVWARN_TABLE, "coordinate")},
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

ODS_FILING_DOCUMENT_INSERT = """
INSERT INTO {table_ref} (
    record_id, data_source, data_type, cik, accession_number,
    sequence, description, filename, size, url, created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type, :cik, :accession_number,
    :sequence, :description, :filename, :size, :url,
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
    cik = EXCLUDED.cik,
    accession_number = EXCLUDED.accession_number,
    sequence = EXCLUDED.sequence,
    description = EXCLUDED.description, filename = EXCLUDED.filename,
    size = EXCLUDED.size, url = EXCLUDED.url,
    updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_FINANCIAL_FACT_INSERT = """
INSERT INTO {table_ref} (
    record_id, data_source, data_type, cik, entity_name, taxonomy, concept,
    concept_label, unit, value, accn, form, filed, fy, fp, frame,
    start_date, end_date, is_amendment, created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type, :cik, :entity_name, :taxonomy, :concept,
    :concept_label, :unit, CAST(:value AS numeric), :accn, :form,
    CAST(:filed AS date), CAST(:fy AS integer), :fp, :frame,
    CAST(:start_date AS date), CAST(:end_date AS date),
    COALESCE(CAST(:is_amendment AS boolean), FALSE),
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
    cik = EXCLUDED.cik, entity_name = EXCLUDED.entity_name,
    taxonomy = EXCLUDED.taxonomy, concept = EXCLUDED.concept,
    concept_label = EXCLUDED.concept_label, unit = EXCLUDED.unit,
    value = EXCLUDED.value, accn = EXCLUDED.accn, form = EXCLUDED.form,
    filed = EXCLUDED.filed, fy = EXCLUDED.fy, fp = EXCLUDED.fp,
    frame = EXCLUDED.frame, start_date = EXCLUDED.start_date,
    end_date = EXCLUDED.end_date, is_amendment = EXCLUDED.is_amendment,
    updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_COMPANY_STANDARD_INSERT = """
INSERT INTO ts_ods.ods_company (
    record_id, data_source, data_type, cik, name, entity_type, exchanges, tickers,
    sic, sic_description, address, website, ein, description, category, phone, former_names, investor_website, created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type, :cik, :name, :entity_type, CAST(:exchanges AS jsonb),
    CAST(:tickers AS jsonb), :sic, :sic_description, CAST(:address AS jsonb),
    :website, :ein, :description, :category, :phone, CAST(:former_names AS jsonb),
    :investor_website,
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
    cik = EXCLUDED.cik, name = EXCLUDED.name, entity_type = EXCLUDED.entity_type,
    exchanges = EXCLUDED.exchanges, tickers = EXCLUDED.tickers, sic = EXCLUDED.sic,
    sic_description = EXCLUDED.sic_description, address = EXCLUDED.address,
    website = EXCLUDED.website, updated_at = EXCLUDED.updated_at,
    ein = EXCLUDED.ein, description = EXCLUDED.description, category = EXCLUDED.category,
    phone = EXCLUDED.phone, former_names = EXCLUDED.former_names,
    investor_website = EXCLUDED.investor_website
RETURNING *
"""

ODS_FILING_STANDARD_INSERT = """
INSERT INTO ts_ods.ods_filing (
    record_id, data_source, data_type, cik, accession_number, form, filing_date,
    report_date, acceptance_datetime, act, file_number, film_number, items,
    core_type, size, is_xbrl, is_inline_xbrl, filing_base, filing_index_url,
    created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type, :cik, :accession_number, :form,
    CAST(:filing_date AS date), CAST(:report_date AS date),
    CAST(:acceptance_datetime AS timestamptz), :act, :file_number, :film_number, :items,
    :core_type, CAST(:size AS bigint), CAST(:is_xbrl AS boolean),
    CAST(:is_inline_xbrl AS boolean), :filing_base, :filing_index_url,
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
    cik = EXCLUDED.cik, accession_number = EXCLUDED.accession_number,
    form = EXCLUDED.form, filing_date = EXCLUDED.filing_date,
    report_date = EXCLUDED.report_date, acceptance_datetime = EXCLUDED.acceptance_datetime,
    act = EXCLUDED.act, file_number = EXCLUDED.file_number,
    film_number = EXCLUDED.film_number, items = EXCLUDED.items,
    core_type = EXCLUDED.core_type, size = EXCLUDED.size,
    is_xbrl = EXCLUDED.is_xbrl, is_inline_xbrl = EXCLUDED.is_inline_xbrl,
    filing_base = EXCLUDED.filing_base, filing_index_url = EXCLUDED.filing_index_url,
    updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_SOCIAL_ACCOUNT_INSERT = """
INSERT INTO ts_ods.ods_social_account (
    record_id, data_source, data_type, account_id, platform, platform_account_id,
    username, display_name, profile_url, avatar_url, bio, account_type,
    is_verified, is_private, account_created_at, follower_count, following_count,
    content_count, location, language, extra, captured_at,
    created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type, :account_id, :platform, :platform_account_id,
    :username, :display_name, :profile_url, :avatar_url, :bio, :account_type,
    :is_verified, :is_private, CAST(:account_created_at AS timestamptz),
    :follower_count, :following_count, :content_count, :location, :language,
    CAST(:extra AS jsonb), CAST(:captured_at AS timestamptz),
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
    account_id = EXCLUDED.account_id, platform = EXCLUDED.platform,
    platform_account_id = EXCLUDED.platform_account_id, username = EXCLUDED.username,
    display_name = EXCLUDED.display_name, profile_url = EXCLUDED.profile_url,
    avatar_url = EXCLUDED.avatar_url, bio = EXCLUDED.bio,
    account_type = EXCLUDED.account_type, is_verified = EXCLUDED.is_verified,
    is_private = EXCLUDED.is_private, account_created_at = EXCLUDED.account_created_at,
    follower_count = EXCLUDED.follower_count, following_count = EXCLUDED.following_count,
    content_count = EXCLUDED.content_count, location = EXCLUDED.location,
    language = EXCLUDED.language, extra = EXCLUDED.extra,
    captured_at = EXCLUDED.captured_at, updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_SOCIAL_CONTENT_INSERT = """
INSERT INTO ts_ods.ods_social_content (
    record_id, data_source, data_type, content_id, platform, platform_content_id,
    content_type, author_account_id, parent_content_id, root_content_id,
    text_content, language, content_url, published_at, edited_at, deleted_at,
    reply_count, like_count, repost_count, quote_count, view_count, is_sensitive,
    extra, captured_at, created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type, :content_id, :platform, :platform_content_id,
    :content_type, :author_account_id, :parent_content_id, :root_content_id,
    :text_content, :language, :content_url, CAST(:published_at AS timestamptz),
    CAST(:edited_at AS timestamptz), CAST(:deleted_at AS timestamptz),
    :reply_count, :like_count, :repost_count, :quote_count, :view_count,
    :is_sensitive, CAST(:extra AS jsonb), CAST(:captured_at AS timestamptz),
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
    content_id = EXCLUDED.content_id, platform = EXCLUDED.platform,
    platform_content_id = EXCLUDED.platform_content_id, content_type = EXCLUDED.content_type,
    author_account_id = EXCLUDED.author_account_id, parent_content_id = EXCLUDED.parent_content_id,
    root_content_id = EXCLUDED.root_content_id, text_content = EXCLUDED.text_content,
    language = EXCLUDED.language, content_url = EXCLUDED.content_url,
    published_at = EXCLUDED.published_at, edited_at = EXCLUDED.edited_at,
    deleted_at = EXCLUDED.deleted_at, reply_count = EXCLUDED.reply_count,
    like_count = EXCLUDED.like_count, repost_count = EXCLUDED.repost_count,
    quote_count = EXCLUDED.quote_count, view_count = EXCLUDED.view_count,
    is_sensitive = EXCLUDED.is_sensitive, extra = EXCLUDED.extra,
    captured_at = EXCLUDED.captured_at, updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_SOCIAL_MEDIA_INSERT = """
INSERT INTO ts_ods.ods_social_media (
    record_id, data_source, data_type, content_id, media_index, media_type,
    platform_media_id, media_url, preview_url, width, height, duration_ms,
    alt_text, extra, created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type, :content_id, :media_index, :media_type,
    :platform_media_id, :media_url, :preview_url, :width, :height, :duration_ms,
    :alt_text, CAST(:extra AS jsonb), CAST(:created_at AS timestamptz),
    CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
    content_id = EXCLUDED.content_id, media_index = EXCLUDED.media_index,
    media_type = EXCLUDED.media_type, platform_media_id = EXCLUDED.platform_media_id,
    media_url = EXCLUDED.media_url, preview_url = EXCLUDED.preview_url,
    width = EXCLUDED.width, height = EXCLUDED.height, duration_ms = EXCLUDED.duration_ms,
    alt_text = EXCLUDED.alt_text, extra = EXCLUDED.extra, updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_SOCIAL_HASHTAG_INSERT = """
INSERT INTO ts_ods.ods_social_hashtag (
    record_id, data_source, data_type, hashtag_id, platform, hashtag_text,
    normalized_text, created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type, :hashtag_id, :platform, :hashtag_text,
    :normalized_text, CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
    hashtag_id = EXCLUDED.hashtag_id, platform = EXCLUDED.platform,
    hashtag_text = EXCLUDED.hashtag_text, normalized_text = EXCLUDED.normalized_text,
    updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_SOCIAL_CONTENT_HASHTAG_INSERT = """
INSERT INTO ts_ods.ods_social_content_hashtag (
    record_id, data_source, data_type, content_id, hashtag_id, hashtag_index,
    created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type, :content_id, :hashtag_id, :hashtag_index,
    CAST(:created_at AS timestamptz), CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
    content_id = EXCLUDED.content_id, hashtag_id = EXCLUDED.hashtag_id,
    hashtag_index = EXCLUDED.hashtag_index, updated_at = EXCLUDED.updated_at
RETURNING *
"""

ODS_SOCIAL_INTERACTION_INSERT = """
INSERT INTO ts_ods.ods_social_interaction (
    record_id, data_source, data_type, interaction_id, platform, interaction_type,
    actor_account_id, source_content_id, target_content_id, occurred_at,
    extra, captured_at, created_at, updated_at
) VALUES (
    :record_id, :data_source, :data_type, :interaction_id, :platform, :interaction_type,
    :actor_account_id, :source_content_id, :target_content_id,
    CAST(:occurred_at AS timestamptz), CAST(:extra AS jsonb),
    CAST(:captured_at AS timestamptz), CAST(:created_at AS timestamptz),
    CAST(:updated_at AS timestamptz)
)
ON CONFLICT (record_id, data_source, data_type) DO UPDATE SET
    interaction_id = EXCLUDED.interaction_id, platform = EXCLUDED.platform,
    interaction_type = EXCLUDED.interaction_type, actor_account_id = EXCLUDED.actor_account_id,
    source_content_id = EXCLUDED.source_content_id, target_content_id = EXCLUDED.target_content_id,
    occurred_at = EXCLUDED.occurred_at, extra = EXCLUDED.extra,
    captured_at = EXCLUDED.captured_at, updated_at = EXCLUDED.updated_at
RETURNING *
"""

_ODS_INSERT_SQL = {
    "news": ODS_NEWS_INSERT,
    "patent": ODS_PATENT_INSERT,
    "navwarn": ODS_NAVWARN_INSERT,
    "intelligence": ODS_INTELLIGENCE_INSERT,
    "company": ODS_COMPANY_STANDARD_INSERT,
    "filing": ODS_FILING_STANDARD_INSERT,
    "filing_document": ODS_FILING_DOCUMENT_INSERT.format(table_ref=_ODS_FILING_DOCUMENT_TABLE),
    "financial_fact": ODS_FINANCIAL_FACT_INSERT.format(table_ref="ts_ods.ods_financial_fact"),
    "social_account": ODS_SOCIAL_ACCOUNT_INSERT,
    "social_content": ODS_SOCIAL_CONTENT_INSERT,
    "social_media": ODS_SOCIAL_MEDIA_INSERT,
    "social_hashtag": ODS_SOCIAL_HASHTAG_INSERT,
    "social_content_hashtag": ODS_SOCIAL_CONTENT_HASHTAG_INSERT,
    "social_interaction": ODS_SOCIAL_INTERACTION_INSERT,
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

    async def _handler_company(self, message: dict[str, Any]) -> bool:
        return await self._process_ods_record(message, table="company")

    async def _handler_filing(self, message: dict[str, Any]) -> bool:
        return await self._process_ods_record(message, table="filing")

    async def _handler_filing_document(self, message: dict[str, Any]) -> bool:
        return await self._process_ods_record(message, table="filing_document")

    async def _handler_financial_fact(self, message: dict[str, Any]) -> bool:
        return await self._process_ods_record(message, table="financial_fact")

    async def _handler_twitter(self, message: dict[str, Any]) -> bool:
        return await self._process_ods_record(message, table="twitter")

    async def _write_current(
        self,
        *,
        table: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """单条 ODS 入库，带可重试错误重试与显式事务语义。

        session() 上下文已提供 commit/rollback：成功提交、异常回滚。
        此处在连接级瞬时错误（InterfaceError/OperationalError）上做有限重试，
        schema 级错误由上层 _execute_with_table_recovery 处理。
        """
        current_sql = _ODS_INSERT_SQL[table]
        last_error: Exception | None = None

        for attempt in range(_ODS_WRITE_MAX_ATTEMPTS):
            try:
                async with self._pg.session() as session:
                    result = await session.execute(text(current_sql), payload)
                    current_row = result.mappings().first()
                    if attempt > 0:
                        logger.info(
                            "%s ODS write recovered on attempt %d/%d table=%s",
                            self._log_prefix, attempt + 1, _ODS_WRITE_MAX_ATTEMPTS, table,
                        )
                    return dict(current_row) if current_row else None
            except (InterfaceError, OperationalError) as exc:
                last_error = exc
                if attempt < _ODS_WRITE_MAX_ATTEMPTS - 1:
                    delay = _ODS_WRITE_RETRY_WAIT * (2 ** attempt)
                    logger.warning(
                        "%s ODS write retry %d/%d table=%s record_id=%s error=%s",
                        self._log_prefix, attempt + 1, _ODS_WRITE_MAX_ATTEMPTS,
                        table, payload.get("record_id"), exc,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise
        # 理论不可达，防御性抛出
        raise last_error if last_error else RuntimeError("ODS write exhausted retries")

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
            normalized_records = normalizer(raw_data)
            if inspect.isawaitable(normalized_records):
                normalized_records = await normalized_records
            if isinstance(normalized_records, dict):
                normalized_records = [normalized_records]
            if not isinstance(normalized_records, list) or not normalized_records:
                logger.warning(
                    "%s Normalizer returned no records, table=%s data_type=%s "
                    "data_source=%s result_type=%s",
                    self._log_prefix,
                    table,
                    data_type,
                    data_source,
                    type(normalized_records).__name__,
                )
                return False

            now = datetime.now(timezone.utc)
            result = None
            emitted_payload: dict[str, Any] | None = None
            for normalized in normalized_records:
                if not isinstance(normalized, dict):
                    continue
                normalized_record_id = normalized.get("record_id") or record_id
                output_table = normalized.get("data_type") or table
                if not normalized_record_id or output_table not in _ODS_INSERT_SQL:
                    logger.warning("%s Invalid normalized ODS record table=%s", self._log_prefix, output_table)
                    return False
                
                payload = {**normalized, "record_id": normalized_record_id, "data_source": normalized.get("data_source") or data_source, "data_type": output_table, "created_at": now, "updated_at": now}
                result = await self._execute_with_table_recovery(output_table, partial(self._write_current, table=output_table, payload=payload), payload=payload)
                emitted_payload = payload

            if emitted_payload is None:
                return False

            await self._emit(
                result,
                record_id=record_id or emitted_payload["record_id"],
                data_source=result.get("data_source") if result else emitted_payload["data_source"],
                data_type=data_type,
            )
            logger.debug(
                "%s Normalized table=ods_%s record_id=%s source=%s",
                self._log_prefix,
                table,
                record_id or emitted_payload["record_id"],
                emitted_payload["data_source"],
            )
            return True
        except Exception:
            logger.exception("%s Failed to normalize table=%s", self._log_prefix, table)
            return False
