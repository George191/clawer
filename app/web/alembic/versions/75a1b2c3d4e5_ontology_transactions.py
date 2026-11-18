"""Persist atomic multi-object action receipts and protected field-level history."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "75a1b2c3d4e5"
down_revision = "74a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ontology_action_events", sa.Column("changes", JSONB(), nullable=True))
    op.add_column(
        "ontology_action_events",
        sa.Column("restricted_history", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "ontology_transactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("team.id"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("steps", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("undone_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("project_id", "actor_id", "request_id", name="uq_ontology_tx_request"),
    )
    op.create_index(
        "ix_ontology_tx_history",
        "ontology_transactions",
        ["project_id", "actor_id", "created_at", "id"],
    )
    op.add_column(
        "ontology_action_events",
        sa.Column("transaction_id", sa.Uuid(), sa.ForeignKey("ontology_transactions.id")),
    )


def downgrade():
    op.drop_column("ontology_action_events", "transaction_id")
    op.drop_table("ontology_transactions")
    op.drop_column("ontology_action_events", "restricted_history")
    op.drop_column("ontology_action_events", "changes")
