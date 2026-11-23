"""External source registry, installed only by the explicit migration tool."""

_ENGINE_CHECK = "CONSTRAINT ck_ontology_source_engine_v2 CHECK (engine IN ({}))"
_ENGINES_V2 = "'postgresql','mysql','oracle','dameng','elasticsearch','milvus','redis','clickhouse','s3'"
ENGINE_CHECK = _ENGINE_CHECK.format(_ENGINES_V2)
ENGINE_CHECK_MONGODB = _ENGINE_CHECK.format(_ENGINES_V2 + ",'mongodb'")

DATA_CONNECTIONS_DDL = f"""
CREATE TABLE public.ontology_data_connections (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    engine TEXT NOT NULL {ENGINE_CHECK_MONGODB},
    config JSONB NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    credentials_ciphertext TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    tested_at TIMESTAMPTZ,
    paused_at TIMESTAMPTZ,
    CONSTRAINT ck_ontology_pg_source_pause CHECK (paused_at IS NULL OR (engine = 'postgresql' AND tested_at IS NULL))
)
"""

DATA_CONNECTIONS_ENGINE_DDL = f"""
ALTER TABLE public.ontology_data_connections
  DROP CONSTRAINT ontology_data_connections_engine_check,
  ADD {ENGINE_CHECK}
"""

ENGINE_CHECK_INSTALLED = """SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
    WHERE conrelid=to_regclass('public.ontology_data_connections')
    AND conname='ck_ontology_source_engine_v2' AND contype='c' AND convalidated)"""

DATA_CONNECTIONS_MONGODB_DDL = f"""
ALTER TABLE public.ontology_data_connections
  DROP CONSTRAINT ck_ontology_source_engine_v2,
  ADD {ENGINE_CHECK_MONGODB}
"""

DATA_CONNECTIONS_MONGODB_ENABLED = """
SELECT EXISTS (
    SELECT 1 FROM pg_catalog.pg_constraint
    WHERE conrelid = pg_catalog.to_regclass('public.ontology_data_connections')
      AND conname = 'ck_ontology_source_engine_v2' AND contype = 'c' AND convalidated
      AND pg_catalog.pg_get_constraintdef(oid) LIKE '%''mongodb''::text%'
)
"""


DATA_CONNECTIONS_PAUSE_DDL = """
ALTER TABLE public.ontology_data_connections
  ALTER COLUMN tested_at DROP NOT NULL,
  ADD COLUMN paused_at TIMESTAMPTZ,
  ADD CONSTRAINT ck_ontology_pg_source_pause
    CHECK (paused_at IS NULL OR (engine = 'postgresql' AND tested_at IS NULL))
"""
