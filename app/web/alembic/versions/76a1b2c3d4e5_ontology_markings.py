"""Reusable ontology security markings, grants and historical clearances."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "76a1b2c3d4e5"
down_revision = "75a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ontology_markings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("project_id", sa.Uuid(), sa.ForeignKey("team.id"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("readers", JSONB, nullable=False),
        sa.UniqueConstraint("project_id", "name", name="uq_ontology_marking_name"),
    )
    op.create_table(
        "ontology_marking_changes",
        sa.Column("marking_id", sa.Uuid(), sa.ForeignKey("ontology_markings.id"), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("readers", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    for table in ("ontology_action_events", "ontology_transactions"):
        op.add_column(
            table,
            sa.Column("markings", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        )


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM ontology_markings)")):
        raise RuntimeError("A populated ontology marking policy cannot be discarded by downgrading")
    for table in ("ontology_action_events", "ontology_transactions"):
        op.drop_column(table, "markings")
    op.drop_table("ontology_marking_changes")
    op.drop_table("ontology_markings")
