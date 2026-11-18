"""Save scoped Explorer queries and object identities."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "80a1b2c3d4e5"
down_revision = "79a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "explorer_saved_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "owner_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("schema_name", sa.String(63), nullable=False),
        sa.Column("table_name", sa.String(63), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("shared", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Uuid(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.CheckConstraint("kind IN ('exploration','list')", name="ck_explorer_saved_item_kind"),
    )
    op.create_index(
        "ix_explorer_saved_item_owner_asset",
        "explorer_saved_items",
        ["owner_id", "schema_name", "table_name", "kind", "name", "id"],
    )


def downgrade():
    op.drop_table("explorer_saved_items")
