"""Canonical ETL table definitions and registry seed policy."""

from __future__ import annotations

CANONICAL_ODS_TABLES = frozenset({
    "company", "filing", "filing_document", "financial_fact",
    "contract", "tender",
    "social_account", "social_content", "social_media", "social_hashtag", "social_interaction",
})

RDS_CURRENT_BASELINE_DDLS: dict[str, str] = {
    "contract": """
CREATE TABLE IF NOT EXISTS ts_rds.rds_contract (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    kafka_offset BIGINT,
    kafka_partition INTEGER,
    kafka_topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
) PARTITION BY HASH (record_id, data_source, data_type);
CREATE INDEX IF NOT EXISTS idx_rds_contract_data_source ON ts_rds.rds_contract (data_source);
CREATE INDEX IF NOT EXISTS idx_rds_contract_updated_at ON ts_rds.rds_contract (updated_at DESC);
""".strip(),
    "tender": """
CREATE TABLE IF NOT EXISTS ts_rds.rds_tender (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    kafka_offset BIGINT,
    kafka_partition INTEGER,
    kafka_topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
) PARTITION BY HASH (record_id, data_source, data_type);
CREATE INDEX IF NOT EXISTS idx_rds_tender_data_source ON ts_rds.rds_tender (data_source);
CREATE INDEX IF NOT EXISTS idx_rds_tender_updated_at ON ts_rds.rds_tender (updated_at DESC);
""".strip(),
    "news": """
CREATE TABLE IF NOT EXISTS ts_rds.rds_news (
    record_id TEXT PRIMARY KEY,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    kafka_offset BIGINT,
    kafka_partition INTEGER,
    kafka_topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rds_news_data_source ON ts_rds.rds_news (data_source);
CREATE INDEX IF NOT EXISTS idx_rds_news_created_at ON ts_rds.rds_news (created_at DESC);
""".strip(),
    "patent": """
CREATE TABLE IF NOT EXISTS ts_rds.rds_patent (
    record_id TEXT PRIMARY KEY,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    kafka_offset BIGINT,
    kafka_partition INTEGER,
    kafka_topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rds_patent_data_source ON ts_rds.rds_patent (data_source);
CREATE INDEX IF NOT EXISTS idx_rds_patent_created_at ON ts_rds.rds_patent (created_at DESC);
""".strip(),
    "navwarn": """
CREATE TABLE IF NOT EXISTS ts_rds.rds_navwarn (
    record_id TEXT PRIMARY KEY,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    kafka_offset BIGINT,
    kafka_partition INTEGER,
    kafka_topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rds_navwarn_data_source ON ts_rds.rds_navwarn (data_source);
CREATE INDEX IF NOT EXISTS idx_rds_navwarn_created_at ON ts_rds.rds_navwarn (created_at DESC);
""".strip(),
    "intelligence": """
CREATE TABLE IF NOT EXISTS ts_rds.rds_intelligence (
    record_id TEXT PRIMARY KEY,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    kafka_offset BIGINT,
    kafka_partition INTEGER,
    kafka_topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rds_intelligence_data_source ON ts_rds.rds_intelligence (data_source);
CREATE INDEX IF NOT EXISTS idx_rds_intelligence_created_at ON ts_rds.rds_intelligence (created_at DESC);
""".strip(),
    "company": """
CREATE TABLE IF NOT EXISTS ts_rds.rds_company (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    kafka_offset BIGINT,
    kafka_partition INTEGER,
    kafka_topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
);
CREATE INDEX IF NOT EXISTS idx_rds_company_cik
    ON ts_rds.rds_company ((raw_data->>'cik'));
""".strip(),
    "financial_fact": """
CREATE TABLE IF NOT EXISTS ts_rds.rds_financial_fact (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    kafka_offset BIGINT,
    kafka_partition INTEGER,
    kafka_topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
);
CREATE INDEX IF NOT EXISTS idx_rds_financial_fact_cik
    ON ts_rds.rds_financial_fact ((raw_data->>'cik'));
""".strip(),
    "filing": """
CREATE TABLE IF NOT EXISTS ts_rds.rds_filing (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    raw_data JSONB NOT NULL,
    kafka_offset BIGINT,
    kafka_partition INTEGER,
    kafka_topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
);
CREATE INDEX IF NOT EXISTS idx_rds_filing_accession
    ON ts_rds.rds_filing ((raw_data->>'accession_number'));
""".strip(),
    "account": """
CREATE TABLE IF NOT EXISTS ts_rds.rds_account (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT 'account',
    raw_data JSONB NOT NULL,
    kafka_offset BIGINT,
    kafka_partition INTEGER,
    kafka_topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
) PARTITION BY HASH (record_id, data_source, data_type);
CREATE INDEX IF NOT EXISTS idx_rds_account_source ON ts_rds.rds_account (data_source);
CREATE INDEX IF NOT EXISTS idx_rds_account_updated_at ON ts_rds.rds_account (updated_at DESC);
""".strip(),
    "tweet": """
CREATE TABLE IF NOT EXISTS ts_rds.rds_tweet (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT 'tweet',
    raw_data JSONB NOT NULL,
    kafka_offset BIGINT,
    kafka_partition INTEGER,
    kafka_topic TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
) PARTITION BY HASH (record_id, data_source, data_type);
CREATE INDEX IF NOT EXISTS idx_rds_tweet_source ON ts_rds.rds_tweet (data_source);
CREATE INDEX IF NOT EXISTS idx_rds_tweet_updated_at ON ts_rds.rds_tweet (updated_at DESC);
""".strip(),
}

ODS_CURRENT_BASELINE_DDLS: dict[str, str] = {
    "contract": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_contract (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT 'contract',
    title TEXT,
    content TEXT,
    url TEXT,
    source_created_at TIMESTAMPTZ,
    source_created_at_raw TEXT,
    source_updated_at TIMESTAMPTZ,
    source_updated_at_raw TEXT,
    captured_at TIMESTAMPTZ,
    contract_number TEXT,
    modification_number TEXT,
    transaction_number TEXT,
    referenced_idv_agency_code TEXT,
    referenced_idv_piid TEXT,
    referenced_idv_modification_number TEXT,
    award_or_idv TEXT,
    contract_type TEXT,
    contract_type_code TEXT,
    solicitation_number TEXT,
    solicitation_date DATE,
    award_date DATE,
    period_of_performance_start_date DATE,
    current_completion_date DATE,
    ultimate_completion_date DATE,
    last_date_to_order DATE,
    fiscal_year TEXT,
    agency_code TEXT,
    agency_name TEXT,
    contracting_department_code TEXT,
    contracting_department_name TEXT,
    contracting_office_code TEXT,
    contracting_office_name TEXT,
    funding_agency_code TEXT,
    funding_agency_name TEXT,
    funding_department_code TEXT,
    funding_department_name TEXT,
    funding_office_code TEXT,
    funding_office_name TEXT,
    awardee_name TEXT,
    awardee_uei TEXT,
    awardee_cage_code TEXT,
    awardee_address JSONB,
    place_of_performance JSONB,
    naics_code TEXT,
    psc_code TEXT,
    amount NUMERIC,
    amount_type TEXT,
    action_obligation NUMERIC,
    base_dollars_obligated NUMERIC,
    base_and_exercised_options_value NUMERIC,
    base_and_all_options_value NUMERIC,
    total_action_obligation NUMERIC,
    total_base_and_exercised_options_value NUMERIC,
    total_base_and_all_options_value NUMERIC,
    currency TEXT,
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
) PARTITION BY HASH (record_id, data_source, data_type);
CREATE INDEX IF NOT EXISTS idx_ods_contract_business_id
    ON ts_ods.ods_contract (agency_code, contract_number, modification_number, transaction_number);
CREATE INDEX IF NOT EXISTS idx_ods_contract_award_date ON ts_ods.ods_contract (award_date DESC);
CREATE INDEX IF NOT EXISTS idx_ods_contract_awardee_uei ON ts_ods.ods_contract (awardee_uei);
""".strip(),
    "tender": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_tender (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT 'tender',
    title TEXT,
    content TEXT,
    url TEXT,
    source_published_at TIMESTAMPTZ,
    source_published_at_raw TEXT,
    source_updated_at TIMESTAMPTZ,
    source_updated_at_raw TEXT,
    captured_at TIMESTAMPTZ,
    notice_id TEXT,
    solicitation_number TEXT,
    notice_type TEXT,
    base_type TEXT,
    agency_code TEXT,
    agency_name TEXT,
    naics_code TEXT,
    psc_code TEXT,
    response_deadline TIMESTAMPTZ,
    response_deadline_raw TEXT,
    active BOOLEAN,
    archive_type TEXT,
    archive_date DATE,
    set_aside_code TEXT,
    set_aside TEXT,
    organization_type TEXT,
    description_url TEXT,
    additional_info_url TEXT,
    contacts JSONB,
    place_of_performance JSONB,
    office_address JSONB,
    attachments JSONB,
    attachments_available BOOLEAN,
    award JSONB,
    award_number TEXT,
    award_date DATE,
    award_amount NUMERIC,
    awardee_name TEXT,
    currency TEXT,
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
) PARTITION BY HASH (record_id, data_source, data_type);
CREATE INDEX IF NOT EXISTS idx_ods_tender_notice_id ON ts_ods.ods_tender (data_source, notice_id);
CREATE INDEX IF NOT EXISTS idx_ods_tender_solicitation ON ts_ods.ods_tender (solicitation_number);
CREATE INDEX IF NOT EXISTS idx_ods_tender_deadline ON ts_ods.ods_tender (response_deadline);
""".strip(),
    "financial_fact": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_financial_fact (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT 'financial_fact',
    cik TEXT NOT NULL,
    entity_name TEXT,
    taxonomy TEXT NOT NULL,
    concept TEXT NOT NULL,
    concept_label TEXT,
    unit TEXT NOT NULL,
    value NUMERIC NOT NULL,
    accn TEXT NOT NULL,
    form TEXT NOT NULL,
    filed DATE NOT NULL,
    fy INTEGER,
    fp TEXT,
    frame TEXT,
    start_date DATE,
    end_date DATE,
    is_amendment BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
) PARTITION BY HASH (record_id, data_source, data_type);
CREATE INDEX IF NOT EXISTS idx_fin_fact_accn ON ts_ods.ods_financial_fact (accn);
CREATE INDEX IF NOT EXISTS idx_fin_fact_cik_period ON ts_ods.ods_financial_fact (cik, end_date DESC);
CREATE INDEX IF NOT EXISTS idx_fin_fact_concept ON ts_ods.ods_financial_fact (taxonomy, concept);
CREATE INDEX IF NOT EXISTS idx_fin_fact_filed ON ts_ods.ods_financial_fact (filed DESC);
""".strip(),
    "social_account": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_social_account (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT 'social_account',
    account_id BIGSERIAL NOT NULL,
    platform_account_id TEXT NOT NULL,
    username TEXT,
    display_name TEXT,
    profile_url TEXT,
    avatar_url TEXT,
    bio TEXT,
    account_type TEXT,
    is_verified BOOLEAN,
    is_private BOOLEAN,
    account_created_at TIMESTAMPTZ,
    follower_count BIGINT,
    following_count BIGINT,
    content_count BIGINT,
    location TEXT,
    language TEXT,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    captured_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
) PARTITION BY HASH (record_id, data_source, data_type);
CREATE INDEX IF NOT EXISTS idx_social_account_source_id
    ON ts_ods.ods_social_account (data_source, platform_account_id);
""".strip(),
    "social_content": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_social_content (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT 'social_content',
    content_id BIGSERIAL NOT NULL,
    platform_content_id TEXT NOT NULL,
    content_type TEXT NOT NULL,
    author_account_id BIGINT,
    parent_content_id BIGINT,
    root_content_id BIGINT,
    text_content TEXT,
    language TEXT,
    content_url TEXT,
    published_at TIMESTAMPTZ,
    edited_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    reply_count BIGINT,
    like_count BIGINT,
    repost_count BIGINT,
    quote_count BIGINT,
    view_count BIGINT,
    is_sensitive BOOLEAN,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    captured_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type),
    CHECK (content_type IN ('post', 'comment', 'repost', 'quote'))
) PARTITION BY HASH (record_id, data_source, data_type);
CREATE INDEX IF NOT EXISTS idx_social_content_source_id
    ON ts_ods.ods_social_content (data_source, platform_content_id, record_id);
CREATE INDEX IF NOT EXISTS idx_social_content_author_time
    ON ts_ods.ods_social_content (author_account_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_social_content_parent
    ON ts_ods.ods_social_content (parent_content_id, published_at);
""".strip(),
    "social_media": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_social_media (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT 'social_media',
    content_id BIGINT NOT NULL,
    media_index INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    platform_media_id TEXT,
    source_url TEXT,
    url TEXT,
    width INTEGER,
    height INTEGER,
    duration_ms BIGINT,
    alt_text TEXT,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
) PARTITION BY HASH (record_id, data_source, data_type);
CREATE INDEX IF NOT EXISTS idx_social_media_content
    ON ts_ods.ods_social_media (content_id, media_index);
""".strip(),
    "social_hashtag": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_social_hashtag (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT 'social_hashtag',
    hashtag_id BIGSERIAL NOT NULL,
    hashtag_text TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
) PARTITION BY HASH (record_id, data_source, data_type);
CREATE INDEX IF NOT EXISTS idx_social_hashtag_source
    ON ts_ods.ods_social_hashtag (data_source, normalized_text);
""".strip(),
    "social_content_hashtag": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_social_content_hashtag (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT 'social_content_hashtag',
    content_id BIGINT NOT NULL,
    hashtag_id BIGINT NOT NULL,
    hashtag_index INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
) PARTITION BY HASH (record_id, data_source, data_type);
CREATE INDEX IF NOT EXISTS idx_social_content_hashtag_content
    ON ts_ods.ods_social_content_hashtag (content_id, hashtag_id);
""".strip(),
    "social_interaction": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_social_interaction (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL DEFAULT 'social_interaction',
    interaction_id BIGSERIAL NOT NULL,
    interaction_type TEXT NOT NULL,
    actor_account_id BIGINT,
    source_content_id BIGINT,
    target_content_id BIGINT NOT NULL,
    occurred_at TIMESTAMPTZ,
    extra JSONB NOT NULL DEFAULT '{}'::jsonb,
    captured_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type),
    CHECK (interaction_type IN ('like', 'comment', 'repost', 'quote', 'bookmark'))
) PARTITION BY HASH (record_id, data_source, data_type);
CREATE INDEX IF NOT EXISTS idx_social_interaction_target
    ON ts_ods.ods_social_interaction (target_content_id, interaction_type);
CREATE INDEX IF NOT EXISTS idx_social_interaction_actor
    ON ts_ods.ods_social_interaction (actor_account_id, occurred_at DESC);
""".strip(),
    "news": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_news (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    title TEXT,
    url TEXT,
    source_url TEXT,
    source_published_at TIMESTAMPTZ,
    source_updated_at TIMESTAMPTZ,
    summary TEXT,
    content TEXT,
    content_html TEXT,
    summary_html TEXT,
    author TEXT,
    news_type JSONB,
    organization JSONB,
    tags JSONB,
    external_links JSONB,
    attachments JSONB,
    images JSONB,
    slides JSONB,
    thumbnail TEXT,
    videos JSONB,
    iframes JSONB,
    audios JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
);
CREATE INDEX IF NOT EXISTS idx_ods_news_data_source ON ts_ods.ods_news (data_source);
CREATE INDEX IF NOT EXISTS idx_ods_news_published_at ON ts_ods.ods_news (source_published_at DESC);
CREATE INDEX IF NOT EXISTS idx_ods_news_updated_at ON ts_ods.ods_news (updated_at DESC);
""".strip(),
    "patent": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_patent (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    title TEXT,
    publication_number TEXT,
    application_number TEXT,
    assignee TEXT,
    inventor TEXT,
    publication_date DATE,
    filing_date DATE,
    priority_date DATE,
    grant_date DATE,
    abstract TEXT,
    claims JSONB,
    legal_status TEXT,
    ipc_classification TEXT,
    cpc_classification TEXT,
    patent_type TEXT,
    url TEXT,
    thumbnail TEXT,
    figures JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
);
CREATE INDEX IF NOT EXISTS idx_ods_patent_data_source ON ts_ods.ods_patent (data_source);
CREATE INDEX IF NOT EXISTS idx_ods_patent_publication_date ON ts_ods.ods_patent (publication_date DESC);
CREATE INDEX IF NOT EXISTS idx_ods_patent_updated_at ON ts_ods.ods_patent (updated_at DESC);
""".strip(),
    "navwarn": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_navwarn (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    navarea_id INTEGER,
    warning_no TEXT,
    serial_number INTEGER,
    warning_year INTEGER,
    region TEXT,
    sub_region TEXT,
    oceans TEXT,
    dnc_region TEXT,
    issued_at TIMESTAMPTZ,
    message_text TEXT,
    category TEXT,
    status TEXT,
    coordinate geography(Geometry, 4326),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
);
CREATE INDEX IF NOT EXISTS idx_ods_navwarn_data_source ON ts_ods.ods_navwarn (data_source);
CREATE INDEX IF NOT EXISTS idx_ods_navwarn_issued_at ON ts_ods.ods_navwarn (issued_at DESC);
CREATE INDEX IF NOT EXISTS idx_ods_navwarn_updated_at ON ts_ods.ods_navwarn (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ods_navwarn_coordinate ON ts_ods.ods_navwarn USING GIST (coordinate);
""".strip(),
    "intelligence": """
CREATE TABLE IF NOT EXISTS ts_ods.ods_intelligence (
    record_id TEXT NOT NULL,
    data_source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    title TEXT,
    url TEXT,
    source_published_at TIMESTAMPTZ,
    source_updated_at TIMESTAMPTZ,
    summary TEXT,
    file_name TEXT,
    file_size TEXT,
    file_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (record_id, data_source, data_type)
);
CREATE INDEX IF NOT EXISTS idx_ods_intelligence_data_source ON ts_ods.ods_intelligence (data_source);
CREATE INDEX IF NOT EXISTS idx_ods_intelligence_published_at ON ts_ods.ods_intelligence (source_published_at DESC);
CREATE INDEX IF NOT EXISTS idx_ods_intelligence_updated_at ON ts_ods.ods_intelligence (updated_at DESC);
""".strip(),
    "company": """
CREATE TABLE "ts_ods"."ods_company" (
  "record_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "data_source" text COLLATE "pg_catalog"."default" NOT NULL,
  "data_type" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'financial_company'::text,
  "cik" text COLLATE "pg_catalog"."default" NOT NULL,
  "name" text COLLATE "pg_catalog"."default" NOT NULL,
  "entity_type" text COLLATE "pg_catalog"."default",
  "exchanges" jsonb,
  "tickers" jsonb,
  "sic" text COLLATE "pg_catalog"."default",
  "sic_description" text COLLATE "pg_catalog"."default",
  "address" jsonb,
  "website" text COLLATE "pg_catalog"."default",
  "created_at" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_at" timestamptz(6) NOT NULL DEFAULT now(),
  "ein" text COLLATE "pg_catalog"."default",
  "description" text COLLATE "pg_catalog"."default",
  "category" text COLLATE "pg_catalog"."default",
  "phone" text COLLATE "pg_catalog"."default",
  "former_names" jsonb,
  "investor_website" text COLLATE "pg_catalog"."default",
  PRIMARY KEY ("record_id", "data_source", "data_type")
)
PARTITION BY HASH (
  "record_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops",
  "data_source" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops",
  "data_type" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops"
)
;
-- ----------------------------
-- Indexes structure for table ods_company
-- ----------------------------
CREATE INDEX IF NOT EXISTS "idx_ods_company_cik" ON "ts_ods"."ods_company" USING btree (
  "cik" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
""".strip(),
    "filing": """
CREATE TABLE "ts_ods"."ods_filing" (
  "record_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "data_source" text COLLATE "pg_catalog"."default" NOT NULL,
  "data_type" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'filing'::text,
  "cik" text COLLATE "pg_catalog"."default",
  "accession_number" text COLLATE "pg_catalog"."default",
  "form" text COLLATE "pg_catalog"."default",
  "filing_date" date,
  "report_date" date,
  "acceptance_datetime" timestamptz(6),
  "act" text COLLATE "pg_catalog"."default",
  "file_number" text COLLATE "pg_catalog"."default",
  "film_number" text COLLATE "pg_catalog"."default",
  "items" text COLLATE "pg_catalog"."default",
  "core_type" text COLLATE "pg_catalog"."default",
  "size" int8,
  "is_xbrl" bool,
  "is_inline_xbrl" bool,
  "filing_base" text COLLATE "pg_catalog"."default",
  "filing_index_url" text COLLATE "pg_catalog"."default",
  "created_at" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_at" timestamptz(6) NOT NULL DEFAULT now(),
  PRIMARY KEY ("record_id", "data_source", "data_type")
)
PARTITION BY HASH (
  "record_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops",
  "data_source" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops",
  "data_type" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops"
)
;
-- ----------------------------
-- Indexes structure for table ods_filing
-- ----------------------------
CREATE INDEX "idx_ods_filing_accession" ON "ts_ods"."ods_filing" USING btree (
  "accession_number" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
CREATE INDEX "idx_ods_filing_cik" ON "ts_ods"."ods_filing" USING btree (
  "cik" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
""".strip(),
    "filing_document": """
CREATE TABLE "ts_ods"."ods_filing_document" (
  "record_id" text COLLATE "pg_catalog"."default" NOT NULL,
  "data_source" text COLLATE "pg_catalog"."default" NOT NULL,
  "data_type" text COLLATE "pg_catalog"."default" NOT NULL DEFAULT 'filing_document'::text,
  "cik" text COLLATE "pg_catalog"."default",
  "accession_number" text COLLATE "pg_catalog"."default",
  "sequence" text COLLATE "pg_catalog"."default",
  "description" text COLLATE "pg_catalog"."default",
  "filename" text COLLATE "pg_catalog"."default",
  "size" text COLLATE "pg_catalog"."default",
  "url" text COLLATE "pg_catalog"."default",
  "source_url" text COLLATE "pg_catalog"."default",
  "created_at" timestamptz(6) NOT NULL DEFAULT now(),
  "updated_at" timestamptz(6) NOT NULL DEFAULT now(),
  "doc_type" text COLLATE "pg_catalog"."default",
  PRIMARY KEY ("record_id", "data_source", "data_type")
)
PARTITION BY HASH (
  "record_id" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops",
  "data_source" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops",
  "data_type" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops"
)
;
-- ----------------------------
-- Indexes structure for table ods_filing_document
-- ----------------------------
CREATE INDEX "idx_ods_filing_document_accession" ON "ts_ods"."ods_filing_document" USING btree (
  "accession_number" COLLATE "pg_catalog"."default" "pg_catalog"."text_ops" ASC NULLS LAST
);
""".strip(),
}


def baseline_current_ddls() -> dict[str, dict[str, str]]:
    return {
        "rds": RDS_CURRENT_BASELINE_DDLS,
        "ods": ODS_CURRENT_BASELINE_DDLS,
    }
