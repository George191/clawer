"""Persist external delivery deduplication and receiver receipts."""

import sqlalchemy as sa
from alembic import op

revision = "77a1b2c3d4e5"
down_revision = "76a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ontology_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "transaction_id", sa.Uuid(), sa.ForeignKey("ontology_transactions.id"), nullable=False
        ),
        sa.Column("receiver", sa.String(64), nullable=False),
        sa.Column("endpoint_hash", sa.String(64), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("receipt_id", sa.Uuid()),
        sa.Column("failure", sa.String(100)),
        sa.UniqueConstraint("transaction_id", "receiver", name="uq_ontology_delivery_destination"),
        sa.CheckConstraint(
            "state IN ('pending','failed','delivered') AND attempts >= 0",
            name="ck_ontology_delivery_state",
        ),
    )


def downgrade():
    op.drop_table("ontology_deliveries")
