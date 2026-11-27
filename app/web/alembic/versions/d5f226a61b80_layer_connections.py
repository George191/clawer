"""Bind each warehouse layer to a saved Data Connection."""

from alembic import op

from app.etl.schema.ontology import ONTOLOGY_DDL_STATEMENTS


# Preserve the SQL contract of this historical revision.
ONTOLOGY_DDL_STATEMENTS = tuple(sql.replace("helio_", "ontology_") for sql in ONTOLOGY_DDL_STATEMENTS)

revision = "d5f226a61b80"
down_revision = "c731f0d2a6b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in ONTOLOGY_DDL_STATEMENTS:
        op.execute(statement)
    op.execute("""CREATE TABLE app.etl_layer_connections (
        layer text PRIMARY KEY CHECK (length(layer) BETWEEN 1 AND 63),
        connection_id uuid NOT NULL REFERENCES app.ontology_data_connections(id) ON DELETE RESTRICT
    )""")
    op.execute("""INSERT INTO app.etl_layer_connections(layer, connection_id)
        SELECT DISTINCT o.layer, b.connection_id
        FROM app.ontology_objects o CROSS JOIN app.etl_business_connection b
        WHERE b.singleton AND o.is_active AND o.layer <> ''""")
    op.execute("""DO $$ BEGIN
        IF to_regclass('app.syncer_watermarks') IS NOT NULL THEN
            UPDATE app.syncer_watermarks w SET source_table=b.connection_id::text || '/' || o.id::text
            FROM app.ontology_objects o JOIN app.etl_layer_connections b ON b.layer=o.layer
            WHERE o.is_active AND o.layer='rds' AND o.table_role='current'
              AND w.source_table=o.schema_name || '.' || o.table_name;
        END IF;
    END $$""")


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF to_regclass('app.syncer_watermarks') IS NOT NULL THEN
            UPDATE app.syncer_watermarks w SET source_table=o.schema_name || '.' || o.table_name
            FROM app.ontology_objects o JOIN app.etl_layer_connections b ON b.layer=o.layer
            WHERE o.is_active AND o.layer='rds' AND o.table_role='current'
              AND w.source_table=b.connection_id::text || '/' || o.id::text;
        END IF;
    END $$""")
    op.drop_table("etl_layer_connections", schema="app")
