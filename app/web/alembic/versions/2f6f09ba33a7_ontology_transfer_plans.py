"""Store draft transfer configurations in the application database."""

import sqlalchemy as sa
from alembic import op

revision = "2f6f09ba33a7"
down_revision = "d4bd0f9e810e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ontology_transfer_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("source_schema", sa.String(63), nullable=False),
        sa.Column("source_table", sa.String(63), nullable=False),
        sa.Column("target_schema", sa.String(63), nullable=False),
        sa.Column("target_table", sa.String(63), nullable=False),
        sa.Column("write_mode", sa.String(6), nullable=False),
        sa.Column("trigger", sa.String(9), nullable=False),
        sa.Column("schedule_time", sa.String(5)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("write_mode IN ('append', 'upsert')", name="ck_ontology_transfer_plans_mode"),
        sa.CheckConstraint(
            "(trigger = 'manual' AND schedule_time IS NULL) OR "
            "(trigger = 'scheduled' AND schedule_time IS NOT NULL AND "
            "schedule_time ~ '^(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]$')",
            name="ck_ontology_transfer_plans_schedule",
        ),
        sa.CheckConstraint("source_schema <> target_schema OR source_table <> target_table",
                           name="ck_ontology_transfer_plans_endpoints"),
    )
    op.create_index("ix_ontology_transfer_plans_source", "ontology_transfer_plans", ["source_schema"])
    op.create_index("ix_ontology_transfer_plans_target", "ontology_transfer_plans", ["target_schema"])


def downgrade() -> None:
    op.drop_table("ontology_transfer_plans")
