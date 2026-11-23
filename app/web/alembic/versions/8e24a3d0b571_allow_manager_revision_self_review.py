"""Allow manager self-review; current project roles are checked by the service."""
from alembic import op

revision = "8e24a3d0b571"
down_revision = "8d13f2c9a460"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_constraint("ck_ontology_revision_independent", "ontology_revision_proposals", type_="check")


def downgrade():
    # Refuse the downgrade if self-reviewed proposals exist; preserve their audit records.
    op.create_check_constraint("ck_ontology_revision_independent", "ontology_revision_proposals",
                               "reviewer_id IS NULL OR reviewer_id != author_id")
