"""Record only the latest bounded diagnostics for the reviewed asset schema."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "3370c8e9bae1"
down_revision = "f27ad8ce609b"
branch_labels = None
depends_on = None

_TABLE = "ontology_asset_quality_observations"
_CHECK = "ck_ontology_asset_quality_schema_drift"


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.add_column(_TABLE, sa.Column("schema_drift", JSONB(none_as_null=True), nullable=True))
    op.create_check_constraint(
        _CHECK, _TABLE, "schema_drift IS NULL OR (kind = 'schema' AND state = 'unhealthy' "
        "AND reason = 'schema_drift' AND jsonb_typeof(schema_drift) = 'object') IS TRUE",
    )


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout='3s'")
    op.execute(f"LOCK TABLE public.{_TABLE} IN ACCESS EXCLUSIVE MODE")
    op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM public.{_TABLE} WHERE schema_drift IS NOT NULL) THEN
                RAISE EXCEPTION 'Cannot downgrade with recorded schema drift diagnostics';
            END IF;
        END $$
    """)
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.drop_column(_TABLE, "schema_drift")
