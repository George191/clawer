"""Track durable scoped asset quality incidents without external notifications."""

import sqlalchemy as sa
from alembic import op

revision = "f32194ec7a60"
down_revision = "e66ab7c415f2"
branch_labels = None
depends_on = None

_INCIDENTS = "ontology_asset_quality_incidents"


def upgrade() -> None:
    op.create_table(
        _INCIDENTS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("target_table", sa.String(63), sa.ForeignKey("ontology_asset_freshness_policies.target_table"),
                  nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("reason", sa.String(16), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True)),
        sa.Column("acknowledged_by", sa.Uuid()),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("close_reason", sa.String(24)),
        sa.CheckConstraint("generation > 0 AND ((kind = 'freshness' AND reason = 'write_overdue') OR "
                           "(kind = 'schema' AND reason = 'schema_drift') OR "
                           "(kind = 'heap_size' AND reason IN ('below_minimum','above_maximum')))",
                           name="ck_ontology_asset_quality_incident_kind"),
        sa.CheckConstraint("(closed_at IS NULL) = (close_reason IS NULL) AND "
                           "(close_reason IS NULL OR close_reason IN "
                           "('healthy','policy_paused','policy_reviewed','schema_baseline_captured')) AND "
                           "(acknowledged_at IS NULL) = (acknowledged_by IS NULL)",
                           name="ck_ontology_asset_quality_incident_lifecycle"),
    )
    op.create_index("uq_ontology_asset_quality_incident_active", _INCIDENTS,
                    ["target_table", "kind"], unique=True, postgresql_where=sa.text("closed_at IS NULL"))
    op.create_index("ix_ontology_asset_quality_incident_history", _INCIDENTS,
                    ["target_table", "closed_at", "id"], postgresql_where=sa.text("closed_at IS NOT NULL"))


def downgrade() -> None:
    op.execute(f"""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM {_INCIDENTS}) THEN
                RAISE EXCEPTION 'Cannot downgrade after recording asset quality incidents';
            END IF;
        END $$;
    """)
    op.drop_table(_INCIDENTS)
