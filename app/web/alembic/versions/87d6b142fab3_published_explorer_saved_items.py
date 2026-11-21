"""Keep Published Explorer work independent of physical tables."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "87d6b142fab3"
down_revision = "86d5a031f9b2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ontology_explorer_saved_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("definition_id", sa.Uuid(), sa.ForeignKey("ontology_registry_definitions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("shared", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Uuid(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.CheckConstraint("kind IN ('exploration','list')", name="ck_ontology_explorer_saved_kind"),
    )
    op.create_index("ix_ontology_explorer_saved_scope", "ontology_explorer_saved_items", ["owner_id", "definition_id", "kind", "name", "id"])


def downgrade():
    op.drop_table("ontology_explorer_saved_items")
