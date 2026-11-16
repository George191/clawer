"""Add isolated ontology business records and action audit events."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d4bd0f9e810e"
down_revision = "7c1a0f0e3d12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create only new application database tables; no changes to warehouse tables."""
    op.create_table(
        "ontology_runtime_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "definition_id",
            sa.Uuid(),
            sa.ForeignKey("ontology_registry_definitions.id"),
            nullable=False,
        ),
        sa.Column("key_digest", sa.String(64), nullable=False),
        sa.Column("values", postgresql.JSONB(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("definition_id", "key_digest", name="uq_ontology_runtime_record_key"),
        sa.CheckConstraint("revision > 0", name="ck_ontology_runtime_record_revision"),
    )
    op.create_index(
        "ix_ontology_runtime_records_list",
        "ontology_runtime_records",
        ["definition_id", "deleted", "id"],
    )
    op.create_table(
        "ontology_action_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "record_id", sa.Uuid(), sa.ForeignKey("ontology_runtime_records.id"), nullable=False
        ),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(6), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("record_id", "revision", name="uq_ontology_action_event_revision"),
        sa.CheckConstraint(
            "kind IN ('create', 'modify', 'delete')", name="ck_ontology_action_event_kind"
        ),
        sa.CheckConstraint("revision > 0", name="ck_ontology_action_event_revision"),
    )


def downgrade() -> None:
    """Remove the isolated runtime schema in reverse dependency order."""
    op.drop_table("ontology_action_events")
    op.drop_table("ontology_runtime_records")
