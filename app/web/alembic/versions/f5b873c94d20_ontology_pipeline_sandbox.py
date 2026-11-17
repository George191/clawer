"""Persist exact immutable revisions for the local visual Builder preview."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "f5b873c94d20"
down_revision = "31d9685bf24a"
branch_labels = None
depends_on = None

_TABLE = "ontology_pipeline_sandbox_revisions"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("generation", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("stream_marker", sa.Uuid(), nullable=False),
        sa.Column("physical_digest", sa.String(64), nullable=False),
        sa.Column("input_version_id", sa.Uuid(), sa.ForeignKey("ontology_cdc_sandbox_versions.receipt_id"), nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("config", JSONB(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("saved_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint("generation > 0 AND config_hash ~ '^[0-9a-f]{64}$' "
                           "AND jsonb_typeof(config) = 'object' AND config ? 'versionId' "
                           "AND config->>'versionId' = input_version_id::text",
                           name="ck_ontology_pipeline_sandbox_revision"),
    )
    op.create_index("ix_ontology_pipeline_sandbox_history", _TABLE, ["stream_marker", "physical_digest", "generation"])


def downgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM {_TABLE}) THEN
                RAISE EXCEPTION 'Cannot downgrade and discard immutable Builder draft revisions';
            END IF;
        END $$;
    """)
    op.drop_table(_TABLE)
