"""Persist optional canonical depth-two rules without rewriting existing flat bindings/jobs."""
import sqlalchemy as sa
from alembic import op

revision = "0f8b2e641a5d"
down_revision = "d2fb47b91a6e"
branch_labels = None
depends_on = None

_BINDING = "ontology_transfer_upstream_triggers"
_JOB = "ontology_transfer_upstream_jobs"

_BINDING_CHECK = ("ck_ontology_transfer_upstream_trigger_group", """((group_operator IS NULL AND group_parent_ids IS NULL)
 OR (group_operator IN ('AND','OR') AND group_operator <> operator
     AND array_ndims(group_parent_ids)=1 AND array_lower(group_parent_ids,1)=1
     AND cardinality(group_parent_ids)=2 AND group_parent_ids[1] < group_parent_ids[2])) IS TRUE""")

_JOB_CHECK = ("ck_ontology_transfer_upstream_job_group", """((group_operator IS NULL AND group_parent_ids IS NULL AND rule_parent_ids IS NULL)
 OR (group_operator IN ('AND','OR') AND group_operator <> operator
     AND array_ndims(group_parent_ids)=1 AND array_lower(group_parent_ids,1)=1
     AND cardinality(group_parent_ids)=2 AND group_parent_ids[1] < group_parent_ids[2]
     AND array_ndims(rule_parent_ids)=1 AND array_lower(rule_parent_ids,1)=1
     AND cardinality(rule_parent_ids)=3 AND rule_parent_ids[1] < rule_parent_ids[2]
     AND rule_parent_ids[2] < rule_parent_ids[3] AND rule_parent_ids @> group_parent_ids)) IS TRUE""")



def upgrade() -> None:
    for table in (_BINDING, _JOB):
        op.add_column(table, sa.Column("group_operator", sa.String(3)))
        op.add_column(table, sa.Column("group_parent_ids", sa.ARRAY(sa.Uuid())))
    op.add_column(_JOB, sa.Column("rule_parent_ids", sa.ARRAY(sa.Uuid())))
    for table, check in ((_BINDING, _BINDING_CHECK), (_JOB, _JOB_CHECK)):
        op.create_check_constraint(check[0], table, check[1])


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '3s'")
    op.execute(f"LOCK TABLE {_BINDING}, {_JOB} IN ACCESS EXCLUSIVE MODE")
    for table in (_BINDING, _JOB):
        if op.get_bind().scalar(sa.text(f"SELECT 1 FROM {table} WHERE group_operator IS NOT NULL LIMIT 1")):
            raise RuntimeError("Cannot flatten a configured nested upstream rule or its historical job")
    for table, check in ((_JOB, _JOB_CHECK), (_BINDING, _BINDING_CHECK)):
        op.drop_constraint(check[0], table, type_="check")
        op.drop_column(table, "group_parent_ids")
        op.drop_column(table, "group_operator")
    op.drop_column(_JOB, "rule_parent_ids")
