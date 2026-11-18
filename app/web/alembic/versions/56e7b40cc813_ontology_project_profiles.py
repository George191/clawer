"""Name team-scoped ontology projects and record their accountable owners."""

import sqlalchemy as sa
from alembic import op

revision = "56e7b40cc813"
down_revision = "3370c8e9bae1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ontology_project_profiles",
        sa.Column("team_id", sa.Uuid(), sa.ForeignKey("team.id"), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id"), nullable=False),
    )


def downgrade():
    op.drop_table("ontology_project_profiles")
