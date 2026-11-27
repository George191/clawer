"""Select the ETL business database through the system connection registry."""

from alembic import op
from app.etl.schema.data_connections import DATA_CONNECTIONS_DDL

# Preserve the SQL contract of this historical revision.
DATA_CONNECTIONS_DDL = DATA_CONNECTIONS_DDL.replace("helio_", "ontology_")

revision = "a11e7b9d6204"
down_revision = "cd0137ef6a24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(DATA_CONNECTIONS_DDL.replace("CREATE TABLE app.", "CREATE TABLE IF NOT EXISTS app.", 1))
    op.execute("""CREATE TABLE app.etl_business_connection (
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
        connection_id uuid NOT NULL REFERENCES app.ontology_data_connections(id) ON DELETE RESTRICT,
        updated_at timestamptz NOT NULL DEFAULT now()
    )""")


def downgrade() -> None:
    op.drop_table("etl_business_connection", schema="app")
