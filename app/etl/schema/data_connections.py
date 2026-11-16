"""External source registry, installed only by the explicit migration tool."""

DATA_CONNECTIONS_DDL = """
CREATE TABLE public.ontology_data_connections (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    engine TEXT NOT NULL CHECK (engine IN ('postgresql','mysql','oracle','dameng','elasticsearch','milvus')),
    config JSONB NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    credentials_ciphertext TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    tested_at TIMESTAMPTZ NOT NULL
)
"""
