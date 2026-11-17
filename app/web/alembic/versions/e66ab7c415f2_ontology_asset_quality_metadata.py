"""Add reviewed schema and physical main-heap check evidence to asset policies."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "e66ab7c415f2"
down_revision = "d91a24c73f60"
branch_labels = None
depends_on = None

_POLICIES = "ontology_asset_freshness_policies"
_OBSERVATIONS = "ontology_asset_quality_observations"
_TRANSITIONS = "ontology_asset_quality_transitions"
_BOUNDS = ("(heap_min_bytes IS NULL OR heap_min_bytes >= 0) AND "
           "(heap_max_bytes IS NULL OR heap_max_bytes >= 0) AND "
           "(heap_min_bytes IS NULL OR heap_max_bytes IS NULL OR heap_min_bytes <= heap_max_bytes)")
_CHECK = ("kind IN ('schema','heap_size') AND state IN ('healthy','unhealthy','unknown') "
          "AND generation > 0 AND (heap_bytes IS NULL OR (kind = 'heap_size' AND heap_bytes >= 0))")


def upgrade() -> None:
    op.add_column(_POLICIES, sa.Column("schema_check_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column(_POLICIES, "schema_check_enabled", server_default=None)
    op.add_column(_POLICIES, sa.Column("schema_baseline", JSONB()))
    op.add_column(_POLICIES, sa.Column("heap_min_bytes", sa.BigInteger()))
    op.add_column(_POLICIES, sa.Column("heap_max_bytes", sa.BigInteger()))
    op.create_check_constraint("ck_ontology_asset_heap_size_bounds", _POLICIES, _BOUNDS)
    for table, current in ((_OBSERVATIONS, True), (_TRANSITIONS, False)):
        op.create_table(
            table,
            *([] if current else [sa.Column("id", sa.Uuid(), primary_key=True)]),
            sa.Column("target_table", sa.String(63), sa.ForeignKey(f"{_POLICIES}.target_table"),
                      nullable=False, primary_key=current),
            sa.Column("kind", sa.String(12), nullable=False, primary_key=current),
            sa.Column("generation", sa.Integer(), nullable=False),
            sa.Column("state", sa.String(15), nullable=False),
            sa.Column("reason", sa.String(40)),
            sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("heap_bytes", sa.BigInteger()),
            sa.CheckConstraint(_CHECK, name=("ck_ontology_asset_quality_observation" if current
                                              else "ck_ontology_asset_quality_transition")),
        )
    op.create_index("ix_ontology_asset_quality_history", _TRANSITIONS,
                    ["target_table", "kind", "generation", "checked_at", "id"])


def downgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM {_POLICIES}
                       WHERE schema_check_enabled OR schema_baseline IS NOT NULL
                          OR heap_min_bytes IS NOT NULL OR heap_max_bytes IS NOT NULL)
               OR EXISTS (SELECT 1 FROM {_TRANSITIONS})
            THEN
                RAISE EXCEPTION 'Cannot downgrade after recording asset quality evidence';
            END IF;
        END $$;
    """)
    op.drop_table(_TRANSITIONS)
    op.drop_table(_OBSERVATIONS)
    op.drop_constraint("ck_ontology_asset_heap_size_bounds", _POLICIES, type_="check")
    for name in ("schema_check_enabled", "schema_baseline", "heap_min_bytes", "heap_max_bytes"):
        op.drop_column(_POLICIES, name)
