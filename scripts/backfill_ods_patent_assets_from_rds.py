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

from app.etl.normalizers.base import apply_asset_path_overrides, safe_str
from app.storage.postgres_client import _resolved_pg_url

logger = logging.getLogger("ods-patent-assets-backfill")

_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$")


def _dsn() -> str:
    return _resolved_pg_url().replace("postgresql+asyncpg://", "postgresql://")


def _validate_table_ref(value: str) -> str:
    if not _TABLE_RE.match(value):
        raise argparse.ArgumentTypeError(f"invalid table reference: {value}")
    return value


def _asset_fields(raw_data: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    merged, asset_paths = apply_asset_path_overrides(raw_data)
    if not asset_paths:
        return None, None, None

    def has_asset_prefix(*prefix: str) -> bool:
        return any(path[:len(prefix)] == prefix for path in asset_paths)

    patent = merged.get("patent") or {}
    pdf = safe_str(patent.get("pdf")) if has_asset_prefix("patent", "pdf") else None
    thumbnail = safe_str(patent.get("thumbnail")) if has_asset_prefix("patent", "thumbnail") else None
    figures = patent.get("figures") if has_asset_prefix("patent", "figures") else None
    figures_json = json.dumps(figures, ensure_ascii=False) if figures else None
    return pdf, thumbnail, figures_json


async def _ensure_temp_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TEMP TABLE IF NOT EXISTS tmp_ods_patent_asset_updates (
            record_id TEXT PRIMARY KEY,
            url TEXT,
            thumbnail TEXT,
            figures TEXT
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
    records: list[tuple[str, str | None, str | None, str | None]],
) -> int:
    if not records:
        return 0

    await _ensure_temp_table(conn)
    async with conn.transaction():
        await conn.execute("TRUNCATE tmp_ods_patent_asset_updates")
        await conn.copy_records_to_table(
            "tmp_ods_patent_asset_updates",
            records=records,
            columns=("record_id", "url", "thumbnail", "figures"),
        )
        result = await conn.execute(
            f"""
            UPDATE {target_table} AS ods
            SET
                url = COALESCE(NULLIF(tmp.url, ''), ods.url),
                thumbnail = COALESCE(NULLIF(tmp.thumbnail, ''), ods.thumbnail),
                figures = COALESCE(NULLIF(tmp.figures, '')::jsonb, ods.figures),
                updated_at = NOW()
            FROM tmp_ods_patent_asset_updates AS tmp
            WHERE ods.record_id = tmp.record_id
              AND (
                  (NULLIF(tmp.url, '') IS NOT NULL AND ods.url IS DISTINCT FROM tmp.url)
                  OR (NULLIF(tmp.thumbnail, '') IS NOT NULL AND ods.thumbnail IS DISTINCT FROM tmp.thumbnail)
                  OR (
                      NULLIF(tmp.figures, '') IS NOT NULL
                      AND ods.figures IS DISTINCT FROM tmp.figures::jsonb
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
    batch: dict[str, tuple[str, str | None, str | None, str | None]] = {}
    samples: list[tuple[str, str | None, str | None, str | None]] = []

    try:
        await read_conn.execute("SET statement_timeout = 0")
        await write_conn.execute("SET statement_timeout = 0")
        query = (
            f"SELECT record_id, raw_data::text AS raw_data "
            f"FROM {args.source_table} "
            f"WHERE raw_data ? 'assets'"
        )

        async with read_conn.transaction(readonly=True):
            stmt = await read_conn.prepare(query)
            async for row in stmt.cursor(prefetch=args.fetch_size):
                if args.limit and scanned >= args.limit:
                    break
                scanned += 1

                raw_data = json.loads(row["raw_data"])
                url, thumbnail, figures = _asset_fields(raw_data)
                if not (url or thumbnail or figures):
                    continue

                record = (row["record_id"], url, thumbnail, figures)
                candidates += 1
                if len(samples) < 5:
                    samples.append(record)

                if args.apply:
                    batch[row["record_id"]] = record
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
        for sample in samples:
            logger.info(
                "sample record_id=%s url=%s thumbnail=%s figures=%s",
                sample[0],
                sample[1],
                sample[2],
                sample[3],
            )
    finally:
        await read_conn.close()
        await write_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill ODS patent asset paths from RDS raw_data.assets",
    )
    parser.add_argument("--source-table", type=_validate_table_ref, default="ts_rds.rds_patent")
    parser.add_argument("--target-table", type=_validate_table_ref, default="ts_ods.ods_patent")
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
