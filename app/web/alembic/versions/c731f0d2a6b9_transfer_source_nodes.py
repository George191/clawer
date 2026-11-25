"""Bind transfer drafts to a saved source node and its exact connection revision."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c731f0d2a6b9"
down_revision = "b82c1a760e53"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("ontology_transfer_plans", sa.Column("source_node", postgresql.JSONB(), nullable=True))
    op.add_column("ontology_transfer_plans", sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("ontology_transfer_plans", "source_schema", type_=sa.String(255), existing_type=sa.String(63))
    op.alter_column("ontology_transfer_plans", "source_table", type_=sa.String(255), existing_type=sa.String(63))
    op.drop_constraint("ck_ontology_transfer_plans_endpoints", "ontology_transfer_plans", type_="check")
    op.create_check_constraint("ck_ontology_transfer_plans_endpoints", "ontology_transfer_plans",
        "source_node IS NOT NULL OR source_schema <> target_schema OR source_table <> target_table")
    op.create_check_constraint("ck_ontology_transfer_plans_source_revision", "ontology_transfer_plans",
        "(source_node IS NULL) = (source_updated_at IS NULL)")
    op.create_check_constraint("ck_ontology_transfer_plans_source_node", "ontology_transfer_plans",
        "source_node IS NULL OR (jsonb_typeof(source_node)='object' "
        "AND source_node->>'connectionId' IS NOT NULL AND source_node->>'kind' IS NOT NULL "
        "AND source_node->>'name' IS NOT NULL AND source_node->>'name'=source_schema)")


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE ontology_transfer_plans IN ACCESS EXCLUSIVE MODE"))
    if bind.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM ontology_transfer_plans WHERE source_node IS NOT NULL)")):
        raise RuntimeError("Reassign or remove saved-source transfer drafts before downgrading")
    op.drop_constraint("ck_ontology_transfer_plans_source_node", "ontology_transfer_plans", type_="check")
    op.drop_constraint("ck_ontology_transfer_plans_source_revision", "ontology_transfer_plans", type_="check")
    op.drop_constraint("ck_ontology_transfer_plans_endpoints", "ontology_transfer_plans", type_="check")
    op.create_check_constraint("ck_ontology_transfer_plans_endpoints", "ontology_transfer_plans",
        "source_schema <> target_schema OR source_table <> target_table")
    op.alter_column("ontology_transfer_plans", "source_schema", type_=sa.String(63), existing_type=sa.String(255))
    op.alter_column("ontology_transfer_plans", "source_table", type_=sa.String(63), existing_type=sa.String(255))
    op.drop_column("ontology_transfer_plans", "source_updated_at")
    op.drop_column("ontology_transfer_plans", "source_node")
