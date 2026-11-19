"""Track catalog visibility and soft deletion of published object types."""

from alembic import op
import sqlalchemy as sa


revision = "86d5a031f9b2"
down_revision = "81a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade():
    for field in ("hidden", "deleted"):
        op.add_column("ontology_registry_definitions", sa.Column(field, sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    for field in ("deleted", "hidden"):
        op.drop_column("ontology_registry_definitions", field)
