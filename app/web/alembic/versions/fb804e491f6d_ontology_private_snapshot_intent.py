"""Add the unprovisioned sender-private frozen snapshot intent ledger."""

from alembic import op
from sqlalchemy import text


revision = "fb804e491f6d"
down_revision = "af834982b6d1"
branch_labels = None
depends_on = None

_SCHEMA = "ontology_snapshot_intent_private"
_TABLES = ("identity", "intents", "records")


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA {_SCHEMA}")
    op.execute(f"REVOKE ALL ON SCHEMA {_SCHEMA} FROM PUBLIC")
    op.execute(f"""
        CREATE TABLE {_SCHEMA}.identity (
            singleton boolean PRIMARY KEY CHECK (singleton),
            marker uuid NOT NULL
        )
    """)
    op.execute(f"""
        CREATE TABLE {_SCHEMA}.intents (
            id uuid PRIMARY KEY,
            actor_id uuid NOT NULL,
            request_id uuid NOT NULL UNIQUE,
            expected_previous_generation bigint NOT NULL CHECK (expected_previous_generation >= 0),
            config_revision uuid NOT NULL,
            payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{{64}}$'),
            scope_digest text NOT NULL CHECK (scope_digest ~ '^[0-9a-f]{{64}}$'),
            row_count integer NOT NULL CHECK (row_count BETWEEN 0 AND 100),
            created_at timestamptz NOT NULL
        )
    """)
    op.execute(f"""
        CREATE TABLE {_SCHEMA}.records (
            intent_id uuid NOT NULL REFERENCES {_SCHEMA}.intents(id),
            ordinal integer NOT NULL CHECK (ordinal BETWEEN 1 AND 100),
            id bigint NOT NULL,
            caption text CHECK (pg_catalog.octet_length(caption) <= 8192),
            PRIMARY KEY (intent_id, ordinal),
            UNIQUE (intent_id, id)
        )
    """)
    op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA {_SCHEMA} FROM PUBLIC")


def downgrade() -> None:
    names = ", ".join(f"ONLY {_SCHEMA}.{table}" for table in _TABLES)
    op.execute(f"LOCK TABLE {names} IN ACCESS EXCLUSIVE MODE NOWAIT")
    if any(op.get_bind().scalar(text(f"SELECT 1 FROM ONLY {_SCHEMA}.{table} LIMIT 1")) for table in _TABLES):
        raise RuntimeError("Refusing to remove the provisioned or populated snapshot intent ledger")
    for table in reversed(_TABLES):
        op.execute(f"DROP TABLE {_SCHEMA}.{table}")
    op.execute(f"DROP SCHEMA {_SCHEMA}")
