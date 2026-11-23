"""Allow Redis, ClickHouse and S3-compatible saved connections."""
from alembic import op
from sqlalchemy import text

from app.etl.schema.data_connections import DATA_CONNECTIONS_ENGINE_DDL, ENGINE_CHECK_INSTALLED

revision = "18f214f739c5"
down_revision = "8e35b4e1c682"
branch_labels = None
depends_on = None


def _lock_registry(bind):
    bind.execute(text("SET LOCAL lock_timeout = '5s'"))
    bind.execute(text("SELECT pg_advisory_xact_lock(hashtext('public.ontology_data_connections_migration'))"))
    return bind.scalar(text("SELECT to_regclass('public.ontology_data_connections') IS NOT NULL"))


def upgrade():
    bind = op.get_bind()
    if _lock_registry(bind) and not bind.scalar(text(ENGINE_CHECK_INSTALLED)):
        op.execute(DATA_CONNECTIONS_ENGINE_DDL)


def downgrade():
    bind = op.get_bind()
    if _lock_registry(bind) and bind.scalar(text(ENGINE_CHECK_INSTALLED)):
        # PostgreSQL validates the restored check atomically, preserving any newer sources on failure.
        op.execute("""ALTER TABLE public.ontology_data_connections
            DROP CONSTRAINT ck_ontology_source_engine_v2,
            ADD CONSTRAINT ontology_data_connections_engine_check CHECK (
                engine IN ('postgresql','mysql','oracle','dameng','elasticsearch','milvus'))""")
