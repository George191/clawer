"""Explicit installation and canonical identities for stored physical-object links."""

import hashlib
import json

from sqlalchemy import text


OBJECT_LINKS_DDL_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS public.ontology_object_links (
        id UUID PRIMARY KEY,
        relation_id BIGINT NOT NULL REFERENCES public.ontology_object_relations(id),
        source_key JSONB NOT NULL CHECK (jsonb_typeof(source_key) = 'object' AND source_key <> '{}'::jsonb),
        target_key JSONB NOT NULL CHECK (jsonb_typeof(target_key) = 'object' AND target_key <> '{}'::jsonb),
        source_hash TEXT NOT NULL CHECK (source_hash ~ '^[0-9a-f]{64}$'),
        target_hash TEXT NOT NULL CHECK (target_hash ~ '^[0-9a-f]{64}$'),
        version BIGINT NOT NULL DEFAULT 1 CHECK (version > 0),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by UUID,
        removed_at TIMESTAMPTZ,
        removed_by UUID,
        UNIQUE (relation_id, source_hash, target_hash)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_ontology_object_links_source
        ON public.ontology_object_links (relation_id, source_hash, id) WHERE removed_at IS NULL""",
    """CREATE INDEX IF NOT EXISTS idx_ontology_object_links_target
        ON public.ontology_object_links (relation_id, target_hash, id) WHERE removed_at IS NULL""",
)


def canonical_object_key(key: dict[str, str]) -> str:
    """Serialize complete keys returned by row_identity_sql, never caller aliases."""
    if not isinstance(key, dict) or not key or any(
        not isinstance(name, str) or not isinstance(value, str) for name, value in key.items()
    ):
        raise ValueError("A canonical object key must contain nonempty string-valued fields")
    return json.dumps(key, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def object_key_hash(key: dict[str, str]) -> str:
    return hashlib.sha256(canonical_object_key(key).encode("utf-8")).hexdigest()


async def ensure_object_links_schema(session):
    """Only explicit installation/migration code calls this; reads never create tables."""
    for statement in OBJECT_LINKS_DDL_STATEMENTS:
        await session.execute(text(statement))
