"""Fence independent reviewed synthetic export destinations."""
import sqlalchemy as sa
from alembic import op

revision = "ad6207fa991b"
down_revision = "51b18bf851dd"
branch_labels = None
depends_on = None

_PLAN = "ontology_table_export_plan"
_GRANTS = "ontology_table_export_grants"
_REQUESTS = "ontology_table_export_requests"
_FK = "fk_table_export_request_grant"
_INDEX = "ix_ontology_table_export_requests_grant_generation"
_TABLES = (_PLAN, _GRANTS, _REQUESTS)


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("destination", sa.String(9), nullable=False, server_default="primary"))
    op.add_column(_PLAN, sa.Column("receiver_proof", sa.String(64), nullable=True))
    op.create_check_constraint("ck_ontology_table_export_plan_destination", _PLAN, """
        (destination = 'primary' AND receiver_proof IS NULL) OR
        (destination = 'secondary' AND (receiver_proof ~ '^[0-9a-f]{64}$') IS TRUE)
    """)
    for table in (_GRANTS, _REQUESTS):
        op.create_check_constraint(f"ck_{table}_destination", table, "destination IN ('primary', 'secondary')")
    op.drop_constraint(_FK, _REQUESTS, type_="foreignkey")
    op.drop_index(_INDEX, table_name=_REQUESTS)
    for table, columns in ((_PLAN, ["destination"]), (_GRANTS, ["destination", "generation"])):
        op.drop_constraint(f"{table}_pkey", table, type_="primary")
        op.create_primary_key(f"{table}_pkey", table, columns)
    op.create_foreign_key(_FK, _REQUESTS, _GRANTS, ["destination", "grant_generation"], ["destination", "generation"])
    op.create_index(_INDEX, _REQUESTS, ["destination", "grant_generation"])


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("SET LOCAL lock_timeout = '2s'"))
    bind.execute(sa.text(f"LOCK TABLE {_PLAN}, {_GRANTS}, {_REQUESTS} IN ACCESS EXCLUSIVE MODE"))
    for table in _TABLES:
        if bind.scalar(sa.text(f"SELECT 1 FROM {table} WHERE destination = 'secondary' LIMIT 1")):
            raise RuntimeError("Cannot remove destination scope while secondary export evidence exists")
    op.drop_constraint(_FK, _REQUESTS, type_="foreignkey")
    op.drop_index(_INDEX, table_name=_REQUESTS)
    for table, column in ((_PLAN, "singleton"), (_GRANTS, "generation")):
        op.drop_constraint(f"{table}_pkey", table, type_="primary")
        op.create_primary_key(f"{table}_pkey", table, [column])
    op.create_foreign_key(_FK, _REQUESTS, _GRANTS, ["grant_generation"], ["generation"])
    op.create_index(_INDEX, _REQUESTS, ["grant_generation"])
    for table in _TABLES:
        op.drop_constraint(f"ck_{table}_destination", table, type_="check")
        op.drop_column(table, "destination")
    op.drop_column(_PLAN, "receiver_proof")
