"""Store sealed, bounded Builder output versions and ordered rows."""

import sqlalchemy as sa
from alembic import op

revision = "b46891e47c52"
down_revision = "f5b873c94d20"
branch_labels = None
depends_on = None

_OUTPUTS = "ontology_pipeline_sandbox_outputs"
_ROWS = "ontology_pipeline_sandbox_output_rows"


def upgrade() -> None:
    op.create_table(
        _OUTPUTS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("request_id", sa.Uuid(), unique=True, nullable=False),
        sa.Column("reviewed_generation", sa.Integer(), sa.ForeignKey("ontology_pipeline_sandbox_revisions.generation"),
                  nullable=False),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.Column("result_hash", sa.String(64), nullable=False),
        sa.Column("input_version_id", sa.Uuid(), sa.ForeignKey("ontology_cdc_sandbox_versions.receipt_id"), nullable=False),
        sa.Column("stream_marker", sa.Uuid(), nullable=False),
        sa.Column("physical_digest", sa.String(64), nullable=False),
        sa.Column("columns", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("input_rows", sa.Integer(), nullable=False),
        sa.Column("output_rows", sa.Integer(), nullable=False),
        sa.Column("output_bytes", sa.Integer(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("committed_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint("reviewed_generation > 0 AND config_hash ~ '^[0-9a-f]{64}$' "
                           "AND result_hash ~ '^[0-9a-f]{64}$' AND input_rows BETWEEN 1 AND 100 "
                           "AND output_rows BETWEEN 0 AND input_rows AND output_bytes BETWEEN 0 AND 65536 "
                           "AND (output_rows > 0 OR output_bytes = 0)", name="ck_ontology_pipeline_sandbox_output_bounds"),
        sa.CheckConstraint("array_ndims(columns) = 1 AND array_lower(columns, 1) = 1 "
                           "AND cardinality(columns) BETWEEN 1 AND 2 AND array_position(columns, NULL) IS NULL "
                           "AND columns <@ ARRAY['id','value']::text[] "
                           "AND (cardinality(columns) = 1 OR columns[1] <> columns[2])",
                           name="ck_ontology_pipeline_sandbox_output_projection"),
    )
    op.create_index("ix_ontology_pipeline_sandbox_outputs_scope", _OUTPUTS,
                    ["stream_marker", "physical_digest", "recorded_at", "id"])
    op.create_table(
        _ROWS,
        sa.Column("output_id", sa.Uuid(), sa.ForeignKey(f"{_OUTPUTS}.id"), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("row_values", sa.ARRAY(sa.Text()), nullable=False),
        sa.CheckConstraint("position BETWEEN 1 AND 100 AND array_ndims(row_values) = 1 AND array_lower(row_values, 1) = 1 "
                           "AND cardinality(row_values) BETWEEN 1 AND 2 AND array_position(row_values, NULL) IS NULL "
                           "AND pg_catalog.octet_length(pg_catalog.convert_to(row_values[1], 'UTF8')) "
                           "+ coalesce(pg_catalog.octet_length(pg_catalog.convert_to(row_values[2], 'UTF8')), 0) <= 65536",
                           name="ck_ontology_pipeline_sandbox_output_row"),
    )


def downgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM {_OUTPUTS}) THEN
                RAISE EXCEPTION 'Cannot downgrade and discard committed Builder outputs';
            END IF;
        END $$;
    """)
    op.drop_table(_ROWS)
    op.drop_table(_OUTPUTS)
