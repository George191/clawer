"""Require an explicit, versioned grant for the local table export pilot."""

import sqlalchemy as sa
from alembic import op

revision = "aa4c7d2e1f90"
down_revision = "5bef50bc72c9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ontology_table_export_grants",
        sa.Column("generation", sa.Integer(), autoincrement=False, primary_key=True),
        sa.Column("id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("action", sa.String(8), nullable=False),
        sa.Column("marking", sa.String(17), nullable=False),
        sa.Column("reason", sa.String(240), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column("plan_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("generation > 0", name="ck_ontology_table_export_grant_generation"),
        sa.CheckConstraint("marking = 'sandbox-synthetic'", name="ck_ontology_table_export_grant_marking"),
        sa.CheckConstraint("action IN ('approved', 'revoked')", name="ck_ontology_table_export_grant_action"),
    )
    op.add_column("ontology_table_export_requests", sa.Column("grant_generation", sa.Integer()))
    op.create_foreign_key("fk_table_export_request_grant", "ontology_table_export_requests",
                          "ontology_table_export_grants", ["grant_generation"], ["generation"])
    op.create_index("ix_ontology_table_export_requests_grant_generation",
                    "ontology_table_export_requests", ["grant_generation"])


def downgrade() -> None:
    # A mixed-version sender without grant validation could otherwise bypass a revoke.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM ontology_table_export_plan) THEN
                RAISE EXCEPTION 'Cannot downgrade past export grants while a reviewed export exists';
            END IF;
        END $$;
    """)
    op.drop_index("ix_ontology_table_export_requests_grant_generation", table_name="ontology_table_export_requests")
    op.drop_constraint("fk_table_export_request_grant", "ontology_table_export_requests", type_="foreignkey")
    op.drop_column("ontology_table_export_requests", "grant_generation")
    op.drop_table("ontology_table_export_grants")
