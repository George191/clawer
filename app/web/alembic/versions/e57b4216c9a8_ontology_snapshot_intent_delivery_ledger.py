"""Keep exact private frozen delivery admissions and confirmed receipt mirrors."""

from alembic import op
from sqlalchemy import text


revision = "e57b4216c9a8"
down_revision = "fb804e491f6d"
branch_labels = None
depends_on = None

_TABLE = "ontology_snapshot_intent_private.deliveries"


def upgrade() -> None:
    # Keep this separate from the original three enrolled intent relations.
    # It remains durable evidence even if an original relation is missing.
    op.execute(f"""
        CREATE TABLE {_TABLE} (
            intent_id uuid PRIMARY KEY,
            request_id uuid NOT NULL UNIQUE,
            scope_digest text NOT NULL CHECK (scope_digest ~ '^[0-9a-f]{{64}}$'),
            payload_hash text NOT NULL CHECK (payload_hash ~ '^[0-9a-f]{{64}}$'),
            expected_previous_generation bigint NOT NULL CHECK (
                expected_previous_generation BETWEEN 0 AND 9223372036854775806
            ),
            delivery_config_revision uuid NOT NULL,
            execution_digest text NOT NULL CHECK (execution_digest ~ '^[0-9a-f]{{64}}$'),
            row_count integer NOT NULL CHECK (row_count BETWEEN 0 AND 100),
            admitted_at timestamptz NOT NULL CHECK (pg_catalog.isfinite(admitted_at)),
            receiver_generation bigint,
            receiver_recorded_at timestamptz,
            receiver_row_count integer,
            CONSTRAINT deliveries_mirrored_receipt_check CHECK (
                (receiver_generation IS NULL AND receiver_recorded_at IS NULL AND receiver_row_count IS NULL)
                OR (
                    receiver_generation IS NOT NULL
                    AND receiver_recorded_at IS NOT NULL
                    AND receiver_row_count IS NOT NULL
                    AND receiver_generation > 0
                    AND receiver_generation - 1 = expected_previous_generation
                    AND pg_catalog.isfinite(receiver_recorded_at)
                    AND receiver_row_count = row_count
                )
            )
        )
    """)
    op.execute(f"REVOKE ALL ON TABLE {_TABLE} FROM PUBLIC")


def downgrade() -> None:
    op.execute(f"LOCK TABLE ONLY {_TABLE} IN ACCESS EXCLUSIVE MODE NOWAIT")
    if op.get_bind().scalar(text(f"SELECT 1 FROM ONLY {_TABLE} LIMIT 1")):
        raise RuntimeError("Refusing to remove the populated snapshot intent delivery ledger")
    op.execute(f"DROP TABLE {_TABLE}")
