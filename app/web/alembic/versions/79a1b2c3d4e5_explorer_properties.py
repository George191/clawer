"""Configure the physical Explorer property mapping independently of personal result layouts."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "79a1b2c3d4e5"
down_revision = "78a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "explorer_property_profiles",
        sa.Column("schema_name", sa.String(63), primary_key=True),
        sa.Column("table_name", sa.String(63), primary_key=True),
        sa.Column("source_key", sa.String(36), primary_key=True),
        sa.Column("version", sa.Uuid(), nullable=False),
        sa.Column("settings", JSONB(), nullable=False),
    )


def downgrade():
    op.drop_table("explorer_property_profiles")
