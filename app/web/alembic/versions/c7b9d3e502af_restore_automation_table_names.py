"""Keep shared automation storage outside Scout's table namespace."""

from importlib import import_module

revision = "c7b9d3e502af"
down_revision = "f6a8c2d901be"
branch_labels = None
depends_on = None

_rename = import_module(
    "app.web.alembic.versions.f6a8c2d901be_product_table_prefixes"
)._rename

TABLE_RENAMES = (
    ("scout_task_logs", "automation_task_logs"),
    ("scout_task_runs", "automation_task_runs"),
    ("scout_task_stats_legacy", "automation_task_stats_legacy"),
    ("scout_tasks", "automation_tasks"),
    ("scout_workflows", "automation_workflows"),
)


def upgrade():
    _rename(TABLE_RENAMES)


def downgrade():
    _rename(tuple((new, old) for old, new in reversed(TABLE_RENAMES)))
