"""Keep semantic Explorer layouts independent of physical source tables."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "88a471f93c62"
down_revision = "87d6b142fab3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ontology_explorer_result_layouts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("definition_id", sa.Uuid(), sa.ForeignKey("ontology_registry_definitions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE")),
        sa.Column("layout", JSONB(), nullable=True),
        sa.Column("version", sa.Uuid(), nullable=False),
        sa.UniqueConstraint("definition_id", "owner_id", name="uq_ontology_explorer_layout_owner", postgresql_nulls_not_distinct=True),
    )


def downgrade():
    op.drop_table("ontology_explorer_result_layouts")
