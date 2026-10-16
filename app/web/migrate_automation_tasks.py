"""Move shared task executions to automation tables after stopping all task writers.

Inspect without changes:
    python -m app.web.migrate_automation_tasks --host <postgres-host>

Apply transactionally, then start the matching web, automation and worker processes:
    python -m app.web.migrate_automation_tasks --host <postgres-host> --apply

Existing databases must first apply app.web.migrate_task_runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
from sqlalchemy.engine import make_url

from app.storage.postgres_client import _resolved_pg_url

DDL_PATH = Path(__file__).with_name("sql") / "migrate_automation_tasks.sql"
TABLE_SUFFIXES = ("tasks", "task_runs", "task_logs", "task_stats_legacy")


async def inspect(connection: asyncpg.Connection) -> dict:
    tables = {}
    for prefix in ("ai_collect", "automation"):
        for suffix in TABLE_SUFFIXES:
            name = f"{prefix}_{suffix}"
            tables[name] = await connection.fetchval(
                "SELECT to_regclass($1) IS NOT NULL", f"public.{name}"
            )
    columns = await connection.fetch(
        """
        SELECT table_name, column_name, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name IN ('ai_collect_tasks', 'automation_tasks')
          AND column_name IN (
              'product_domain', 'task_type', 'task_path', 'template_name',
              'template_version', 'current_run_id', 'inserted_records'
          )
        ORDER BY table_name, column_name
        """
    )
    result = {"tables": tables, "task_columns": [dict(row) for row in columns]}
    if tables["automation_tasks"]:
        result["cleanup_task_exists"] = await connection.fetchval(
            """SELECT EXISTS (
                SELECT 1 FROM public.automation_tasks
                WHERE id = md5('automation:task_log_cleanup')::uuid
            )"""
        )
    return result


async def migrate(*, host: str | None = None, apply: bool = False) -> dict:
    url = make_url(_resolved_pg_url()).set(drivername="postgresql")
    if host:
        url = url.set(host=host)
    connection = await asyncpg.connect(
        url.render_as_string(hide_password=False),
        timeout=10,
        command_timeout=300,
        server_settings={
            "application_name": "automation_tasks_migration",
            "default_transaction_read_only": "off" if apply else "on",
            "lock_timeout": "5000",
            "statement_timeout": "300000" if apply else "10000",
        },
    )
    try:
        result = {
            "host": url.host,
            "database": url.database,
            "applied": apply,
            "before": await inspect(connection),
        }
        if apply:
            async with connection.transaction():
                await connection.execute(DDL_PATH.read_text(encoding="utf-8"))
            result["after"] = await inspect(connection)
        return result
    finally:
        await connection.close(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", help="Override only the configured PostgreSQL host")
    parser.add_argument("--apply", action="store_true", help="Apply the transactional migration")
    args = parser.parse_args()
    result = asyncio.run(migrate(host=args.host, apply=args.apply))
    if args.apply:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        receipt = Path("outputs") / "automation_tasks_migration" / f"{stamp}.json"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["receipt"] = str(receipt.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
