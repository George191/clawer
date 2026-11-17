"""Persist bounded target snapshots owned by actual synthetic CDC receipts."""

import sqlalchemy as sa
from alembic import op

revision = "31d9685bf24a"
down_revision = "984b2ef31a6c"
branch_labels = None
depends_on = None

_VERSIONS = "ontology_cdc_sandbox_versions"
_ROWS = "ontology_cdc_sandbox_version_rows"


def upgrade() -> None:
    op.create_table(
        _VERSIONS,
        sa.Column("receipt_id", sa.Uuid(), sa.ForeignKey("ontology_cdc_sandbox_receipts.id"), primary_key=True),
        sa.Column("stream_marker", sa.Uuid(), nullable=False),
        sa.Column("physical_digest", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("applied_through", sa.BigInteger(), nullable=False),
        sa.Column("rows_captured", sa.Integer(), nullable=False),
        sa.Column("bytes_captured", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("generation = 1 AND applied_through > 0 AND rows_captured BETWEEN 1 AND 100 "
                           "AND bytes_captured BETWEEN rows_captured * 8 AND 65536",
                           name="ck_ontology_cdc_sandbox_version_bounds"),
    )
    op.create_index("ix_ontology_cdc_sandbox_versions_scope", _VERSIONS,
                    ["stream_marker", "physical_digest", "recorded_at", "receipt_id"])
    op.create_table(
        _ROWS,
        sa.Column("version_id", sa.Uuid(), sa.ForeignKey(f"{_VERSIONS}.receipt_id"), primary_key=True),
        sa.Column("entity_id", sa.BigInteger(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.CheckConstraint("pg_catalog.octet_length(value) <= 65528", name="ck_ontology_cdc_version_row_bytes"),
    )


def downgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM {_VERSIONS}) THEN
                RAISE EXCEPTION 'Cannot downgrade and discard captured synthetic dataset versions';
            END IF;
        END $$;
    """)
    op.drop_table(_ROWS)
    op.drop_table(_VERSIONS)
