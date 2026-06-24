CREATE SCHEMA IF NOT EXISTS ts_meta;
CREATE SCHEMA IF NOT EXISTS ts_rds_hist;
CREATE SCHEMA IF NOT EXISTS ts_ods_hist;

CREATE TABLE IF NOT EXISTS ts_meta.ddlregistry (
    id BIGSERIAL PRIMARY KEY,
    layer TEXT NOT NULL,
    table_role TEXT NOT NULL DEFAULT 'current',
    schema_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    ddl_sql TEXT NOT NULL,
    version INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    partition_type TEXT NOT NULL DEFAULT 'none',
    partition_column TEXT NULL,
    partition_granularity TEXT NULL,
    partition_count INTEGER NULL,
    description TEXT NULL,
    updated_by TEXT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ddlregistry_table_role_check
        CHECK (table_role IN ('current', 'history')),
    CONSTRAINT ddlregistry_partition_type_check
        CHECK (partition_type IN ('none', 'range', 'hash')),
    CONSTRAINT ddlregistry_partition_granularity_check
        CHECK (
            partition_granularity IS NULL
            OR partition_granularity IN ('month', 'year')
        ),
    CONSTRAINT ddlregistry_partition_layout_check
        CHECK (
            (
                partition_type = 'none'
                AND partition_column IS NULL
                AND partition_granularity IS NULL
                AND partition_count IS NULL
            )
            OR (
                partition_type = 'range'
                AND partition_column IS NOT NULL
                AND partition_granularity IS NOT NULL
                AND partition_count IS NULL
            )
            OR (
                partition_type = 'hash'
                AND partition_column IS NOT NULL
                AND partition_granularity IS NULL
                AND partition_count IS NOT NULL
                AND partition_count > 0
            )
        )
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ddlregistry_active_table
    ON ts_meta.ddlregistry (layer, table_role, schema_name, table_name)
    WHERE is_active = TRUE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_ddlregistry_version
    ON ts_meta.ddlregistry (layer, table_role, schema_name, table_name, version);

CREATE INDEX IF NOT EXISTS idx_ddlregistry_lookup
    ON ts_meta.ddlregistry (layer, table_role, schema_name, table_name, version DESC);
