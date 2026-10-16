"""Migrate workspace execution batches after stopping the old web/workers.

Inspect without changes:
    python -m app.web.migrate_task_runs --host <postgres-host>

Apply, then deploy/start the matching web and worker code:
    python -m app.web.migrate_task_runs --host <postgres-host> --apply

The transaction preserves the old task counters in ai_collect_task_stats_legacy.
Historical per-execution counters remain NULL because logs cannot recover them.
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

DDL_PATH = Path(__file__).with_name("sql") / "migrate_task_runs.sql"


async def inspect(connection: asyncpg.Connection) -> dict:
    tables = await connection.fetchrow(
        """
        SELECT to_regclass('public.ai_collect_task_runs') IS NOT NULL AS runs_exist,
               to_regclass('public.ai_collect_task_stats_legacy') IS NOT NULL AS backup_exists,
               (SELECT n_live_tup FROM pg_stat_user_tables
                WHERE schemaname = 'public' AND relname = 'ai_collect_task_logs')
                    AS estimated_log_count
        """
    )
    columns = await connection.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'ai_collect_tasks'
          AND column_name IN (
              'current_run_id', 'inserted_records', 'updated_records',
              'deleted_records', 'downloaded_records', 'synced_records'
          )
        ORDER BY column_name
        """
    )
    result = {**dict(tables), "task_columns": [row["column_name"] for row in columns]}
    if result["runs_exist"]:
        result["execution_count"] = await connection.fetchval(
            "SELECT count(*) FROM public.ai_collect_task_runs"
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
            "application_name": "workspace_task_runs_migration",
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
        receipt = Path("outputs") / "task_runs_migration" / f"{stamp}.json"
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["receipt"] = str(receipt.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
