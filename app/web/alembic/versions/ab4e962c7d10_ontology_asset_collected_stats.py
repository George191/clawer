"""Store two bounded statistics observations per reviewed sandbox asset."""
import sqlalchemy as sa
from alembic import op

revision = "ab4e962c7d10"
down_revision = "0f8b2e641a5d"
branch_labels = None
depends_on = None

_TABLE = "ontology_asset_collected_stats"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("target_table", sa.String(63), sa.ForeignKey("ontology_asset_freshness_policies.target_table"),
                  primary_key=True),
        sa.Column("slot", sa.String(10), primary_key=True),
        sa.Column("policy_generation", sa.Integer(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("plan_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_oid", sa.BigInteger(), nullable=False),
        sa.Column("target_filenode", sa.BigInteger(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluation_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("estimated_rows", sa.String(96)),
        sa.Column("analyzed_at", sa.DateTime(timezone=True)),
        sa.Column("analyze_floor", sa.DateTime(timezone=True)),
        sa.Column("reset_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("slot IN ('latest','historical') AND policy_generation > 0 AND target_oid > 0 "
                           "AND target_filenode > 0 AND state IN ('captured','no_stats','stats_reset')",
                           name="ck_asset_collected_stats_scope"),
        sa.CheckConstraint("estimated_rows IS NULL OR estimated_rows ~ '^(0|[1-9][0-9]*)([.][0-9]+)?$'",
                           name="ck_asset_collected_stats_decimal"),
        sa.CheckConstraint("(state = 'captured') = (estimated_rows IS NOT NULL OR analyzed_at IS NOT NULL)",
                           name="ck_asset_collected_stats_measure"),
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.execute(f"LOCK TABLE {_TABLE} IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(sa.text(f"SELECT 1 FROM {_TABLE} LIMIT 1")):
        raise RuntimeError("Cannot remove recorded statistics evidence")
    op.drop_table(_TABLE)
