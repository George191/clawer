from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import asyncpg

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.etl.normalizers.base import html_to_text, safe_str
from app.storage.postgres_client import _resolved_pg_url

logger = logging.getLogger("ods-news-content-backfill")

_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")


def _dsn() -> str:
    return _resolved_pg_url().replace("postgresql+asyncpg://", "postgresql://")


def _validate_table_ref(value: str) -> str:
    if not _TABLE_RE.match(value):
        raise argparse.ArgumentTypeError(f"invalid table reference: {value}")
    return value


def _content_fields(raw_data: dict[str, Any]) -> tuple[str | None, str | None]:
    content_html = safe_str(raw_data.get("content_html"))
    if not content_html:
        return None, None
    return content_html, html_to_text(raw_data.get("content") or content_html)


async def _ensure_temp_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS tmp_ods_news_content_updates (
            record_id TEXT NOT NULL,
            data_source TEXT NOT NULL,
            content TEXT,
            content_html TEXT,
            PRIMARY KEY (record_id, data_source)
        ) ON COMMIT PRESERVE ROWS
        """
    )


def _parse_update_count(result: str) -> int:
    try:
        return int(result.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


async def _apply_batch(
    conn: asyncpg.Connection,
    target_table: str,
    records: list[tuple[str, str, str | None, str | None]],
) -> int:
    if not records:
        return 0

    await _ensure_temp_table(conn)
    async with conn.transaction():
        await conn.execute("TRUNCATE tmp_ods_news_content_updates")
        await conn.copy_records_to_table(
            "tmp_ods_news_content_updates",
            records=records,
            columns=("record_id", "data_source", "content", "content_html"),
        )
        result = await conn.execute(
            f"""
            UPDATE {target_table} AS ods
            SET
                content = COALESCE(NULLIF(tmp.content, ''), ods.content),
                content_html = COALESCE(NULLIF(tmp.content_html, ''), ods.content_html),
                updated_at = NOW()
            FROM tmp_ods_news_content_updates AS tmp
            WHERE ods.record_id = tmp.record_id
              AND ods.data_source = tmp.data_source
              AND (
                  (
                      NULLIF(tmp.content_html, '') IS NOT NULL
                      AND ods.content_html IS DISTINCT FROM tmp.content_html
                  )
                  OR (
                      NULLIF(tmp.content, '') IS NOT NULL
                      AND ods.content IS DISTINCT FROM tmp.content
                  )
              )
            """
        )
    return _parse_update_count(result)


async def run(args: argparse.Namespace) -> None:
    read_conn = await asyncpg.connect(_dsn())
    write_conn = await asyncpg.connect(_dsn())
    scanned = 0
    candidates = 0
    updated = 0
    batch: dict[tuple[str, str], tuple[str, str, str | None, str | None]] = {}
    samples: list[tuple[str, str, str | None]] = []

    try:
        await read_conn.execute("SET statement_timeout = 0")
        await write_conn.execute("SET statement_timeout = 0")
        query = (
            f"SELECT record_id, data_source, raw_data::text AS raw_data "
            f"FROM {args.source_table} "
            f"WHERE raw_data ? 'content_html'"
        )

        async with read_conn.transaction(readonly=True):
            stmt = await read_conn.prepare(query)
            async for row in stmt.cursor(prefetch=args.fetch_size):
                if args.limit and scanned >= args.limit:
                    break
                scanned += 1

                raw_data = json.loads(row["raw_data"])
                content_html, content = _content_fields(raw_data)
                if not content_html:
                    continue

                record = (row["record_id"], row["data_source"], content, content_html)
                candidates += 1
                if len(samples) < 5:
                    samples.append((row["record_id"], row["data_source"], content_html[:120]))

                if args.apply:
                    batch[(row["record_id"], row["data_source"])] = record
                    if len(batch) >= args.batch:
                        updated += await _apply_batch(write_conn, args.target_table, list(batch.values()))
                        logger.info(
                            "progress scanned=%d candidates=%d updated=%d",
                            scanned,
                            candidates,
                            updated,
                        )
                        batch.clear()

        if args.apply and batch:
            updated += await _apply_batch(write_conn, args.target_table, list(batch.values()))

        logger.info(
            "done scanned=%d candidates=%d updated=%d apply=%s",
            scanned,
            candidates,
            updated,
            args.apply,
        )
        for record_id, data_source, content_html in samples:
            logger.info("sample record_id=%s data_source=%s content_html=%s", record_id, data_source, content_html)
    finally:
        await read_conn.close()
        await write_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill ODS news content/content_html from RDS raw_data",
    )
    parser.add_argument("--source-table", type=_validate_table_ref, default="ts_rds.rds_news")
    parser.add_argument("--target-table", type=_validate_table_ref, default="ts_ods.ods_news")
    parser.add_argument("--batch", type=int, default=1000)
    parser.add_argument("--fetch-size", type=int, default=1000)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
