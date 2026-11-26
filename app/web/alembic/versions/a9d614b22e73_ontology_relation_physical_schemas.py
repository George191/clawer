"""Allow registered PostgreSQL schema names in storage relationships."""

from alembic import op

revision = "a9d614b22e73"
down_revision = "d5f226a61b80"
branch_labels = None
depends_on = None


def _replace_identity_checks(schema_pattern, table_pattern, schema_scope):
    for side in ("source", "target"):
        schema_check = f"ontology_object_relations_{side}_schema_check"
        table_check = f"ontology_object_relations_{side}_table_check"
        op.execute(f"""ALTER TABLE IF EXISTS app.ontology_object_relations
            DROP CONSTRAINT IF EXISTS {schema_check},
            DROP CONSTRAINT IF EXISTS {table_check},
            ADD CONSTRAINT {schema_check} CHECK ({side}_schema ~ '{schema_pattern}' AND {schema_scope(side)}),
            ADD CONSTRAINT {table_check} CHECK ({side}_table ~ '{table_pattern}')""")


def upgrade():
    op.execute("DROP INDEX IF EXISTS app.uq_ontology_objects_identity")
    op.execute("""DO $$ BEGIN
        IF to_regclass('app.ontology_objects') IS NOT NULL THEN
            CREATE INDEX IF NOT EXISTS ix_ontology_objects_physical_identity ON app.ontology_objects (schema_name, table_name);
        END IF;
    END $$""")
    identifier = "^[A-Za-z_][A-Za-z0-9_]{0,62}$"
    _replace_identity_checks(r"^[A-Za-z_][A-Za-z0-9_]*([.][A-Za-z_][A-Za-z0-9_]*)*$", identifier,
                             lambda side: f"""length({side}_schema) <= 63 AND left({side}_schema, 3) <> 'pg_'
        AND {side}_schema NOT IN ('app','information_schema','ts_meta','ts_meta_hist')""")
    op.execute("""DO $$ BEGIN
        IF to_regclass('app.ontology_objects') IS NOT NULL THEN
            UPDATE app.ontology_registry_definitions AS definition
            SET source_signature = definition.source_signature || jsonb_build_object('connectionId', previous.connection_id::text)
            FROM app.etl_business_connection AS previous
            WHERE previous.singleton AND definition.source_signature IS NOT NULL
              AND NOT (definition.source_signature ? 'connectionId')
              AND EXISTS (
                  SELECT 1 FROM app.ontology_objects AS object
                  JOIN app.etl_layer_connections AS binding ON binding.layer = object.layer
                  WHERE object.is_active AND object.table_role = 'current'
                    AND object.schema_name = definition.published_definition #>> '{source,schema}'
                    AND object.table_name = definition.published_definition #>> '{source,table}'
                    AND binding.connection_id = previous.connection_id
              )
              AND (SELECT count(*) FROM app.ontology_objects AS object
                   WHERE object.is_active
                     AND object.schema_name = definition.published_definition #>> '{source,schema}'
                     AND object.table_name = definition.published_definition #>> '{source,table}') = 1;
        END IF;
    END $$""")


def downgrade():
    op.execute("""UPDATE app.ontology_registry_definitions
        SET source_signature = source_signature - 'connectionId' WHERE source_signature ? 'connectionId'""")
    op.execute("""DO $$ BEGIN
        IF to_regclass('app.ontology_objects') IS NOT NULL THEN
            CREATE UNIQUE INDEX IF NOT EXISTS uq_ontology_objects_identity ON app.ontology_objects (schema_name, table_name);
        END IF;
    END $$""")
    op.execute("DROP INDEX IF EXISTS app.ix_ontology_objects_physical_identity")
    _replace_identity_checks("^ts_[a-z0-9_]{1,60}$", "^[a-z_][a-z0-9_]{0,62}$",
                             lambda side: f"{side}_schema NOT IN ('ts_meta','ts_meta_hist')")
