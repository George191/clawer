"""Bind saved Dameng sources to externally managed DMDRS Kafka output."""

from alembic import op
from sqlalchemy import text

from app.etl.schema.dameng_cdc import DAMENG_CDC_DDL, DAMENG_CDC_INSTALLED

revision = "cd0137ef6a24"
down_revision = "ab69e2f48a0d"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.scalar(text("SELECT to_regclass('app.ontology_data_connections') IS NOT NULL")) and not bind.scalar(text(DAMENG_CDC_INSTALLED)):
        op.execute(DAMENG_CDC_DDL)


def downgrade():
    bind = op.get_bind()
    if bind.scalar(text(DAMENG_CDC_INSTALLED)):
        if bind.scalar(text("SELECT EXISTS (SELECT 1 FROM app.ontology_dameng_cdc_bindings)")):
            raise RuntimeError("Remove saved DMDRS bindings before downgrading")
        op.drop_table("ontology_dameng_cdc_bindings", schema="app")
