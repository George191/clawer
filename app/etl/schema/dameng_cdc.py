"""Binding to an externally operated DMDRS Kafka output; no replication controls."""

DAMENG_CDC_DDL = """
CREATE TABLE app.helio_dameng_cdc_bindings (
    connection_id UUID PRIMARY KEY REFERENCES app.helio_data_connections(id) ON DELETE RESTRICT,
    source_updated_at TIMESTAMPTZ NOT NULL,
    topic TEXT NOT NULL CHECK (length(topic) BETWEEN 1 AND 249),
    tables JSONB NOT NULL CHECK (jsonb_typeof(tables) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

DAMENG_CDC_INSTALLED = "SELECT to_regclass('app.helio_dameng_cdc_bindings') IS NOT NULL"
