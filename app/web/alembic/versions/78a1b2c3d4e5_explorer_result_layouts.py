"""Store physical Explorer result layouts in the application database."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "78a1b2c3d4e5"
down_revision = "77a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "explorer_result_layouts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("schema_name", sa.String(63), nullable=False),
        sa.Column("table_name", sa.String(63), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE")),
        sa.Column("layout", JSONB(), nullable=True),
        sa.Column("version", sa.Uuid(), nullable=False),
        sa.UniqueConstraint(
            "schema_name",
            "table_name",
            "owner_id",
            name="uq_explorer_layout_asset_owner",
            postgresql_nulls_not_distinct=True,
        ),
    )


def downgrade():
    op.drop_table("explorer_result_layouts")
