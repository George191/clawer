from __future__ import annotations

import asyncio
import logging

from app.storage.etl_metadata_store import ensure_ddlregistry_table
from app.storage.postgres_client import get_pg_client

logger = logging.getLogger(__name__)


CURRENT_TABLE = "ts_ods.ods_navwarn"
HISTORY_TABLE = "ts_ods_hist.ods_navwarn"
CURRENT_PARENT_REPLACEMENT = "ts_ods.ods_navwarn__replacement"
HISTORY_PARENT_REPLACEMENT = "ts_ods_hist.ods_navwarn__replacement"


async def _build_transform_select(
    pg,
    source_table: str,
    *,
    include_history_id: bool,
) -> str:
    has_region = await _column_exists(pg, source_table, "region")
    has_sea_name = await _column_exists(pg, source_table, "sea_name")
    has_latitude = await _column_exists(pg, source_table, "latitude")
    has_longitude = await _column_exists(pg, source_table, "longitude")
    has_coordinate_count = await _column_exists(pg, source_table, "coordinate_count")
    has_coordinates = await _column_exists(pg, source_table, "coordinates")

    if has_region and has_sea_name:
        region_expr = "COALESCE(NULLIF(BTRIM(region), ''), NULLIF(BTRIM(sea_name), '')) AS region"
    elif has_region:
        region_expr = "NULLIF(BTRIM(region), '') AS region"
    elif has_sea_name:
        region_expr = "NULLIF(BTRIM(sea_name), '') AS region"
    else:
        region_expr = "NULL::text AS region"

    latitude_expr = "latitude" if has_latitude else "NULL::double precision AS latitude"
    longitude_expr = "longitude" if has_longitude else "NULL::double precision AS longitude"

    if has_coordinate_count and has_coordinates:
        coordinate_count_expr = """
        COALESCE(
            coordinate_count,
            CASE
                WHEN coordinates IS NULL OR jsonb_typeof(coordinates) <> 'array' THEN 0
                ELSE jsonb_array_length(coordinates)
            END
        ) AS coordinate_count
        """
    elif has_coordinate_count:
        coordinate_count_expr = "coordinate_count"
    elif has_coordinates:
        coordinate_count_expr = """
        CASE
            WHEN coordinates IS NULL OR jsonb_typeof(coordinates) <> 'array' THEN 0
            ELSE jsonb_array_length(coordinates)
        END AS coordinate_count
        """
    else:
        coordinate_count_expr = "0 AS coordinate_count"

    history_prefix = "history_id,\n" if include_history_id else ""
    return f"""
SELECT
    {history_prefix}record_id,
    data_source,
    data_type,
    navarea_id,
    warning_no,
    serial_number,
    warning_year,
    {region_expr},
    issued_at,
    message_text,
    hazard_type,
    {latitude_expr},
    {longitude_expr},
    {coordinate_count_expr},
    quality_score,
    quality_flags,
    created_at,
    updated_at
FROM {source_table}
"""


async def _table_exists(pg, table_ref: str) -> bool:
    schema_name, table_name = table_ref.split(".", 1)
    row = await pg.fetch_one(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = :schema_name
          AND table_name = :table_name
        """,
        {"schema_name": schema_name, "table_name": table_name},
    )
    return row is not None


async def _is_partitioned(pg, table_ref: str) -> bool:
    schema_name, table_name = table_ref.split(".", 1)
    row = await pg.fetch_one(
        """
        SELECT pt.partstrat
        FROM pg_partitioned_table pt
        JOIN pg_class c ON c.oid = pt.partrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = :schema_name
          AND c.relname = :table_name
        """,
        {"schema_name": schema_name, "table_name": table_name},
    )
    return row is not None


async def _column_exists(pg, table_ref: str, column_name: str) -> bool:
    schema_name, table_name = table_ref.split(".", 1)
    row = await pg.fetch_one(
        """
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = :schema_name
          AND table_name = :table_name
          AND column_name = :column_name
        """,
        {
            "schema_name": schema_name,
            "table_name": table_name,
            "column_name": column_name,
        },
    )
    return row is not None


async def _create_current_replacement(pg) -> None:
    await pg.execute(
        f"""
        CREATE TABLE {CURRENT_PARENT_REPLACEMENT} (
            record_id TEXT NOT NULL,
            data_source TEXT NOT NULL,
            data_type TEXT NOT NULL,
            navarea_id INTEGER,
            warning_no TEXT,
            serial_number INTEGER,
            warning_year INTEGER,
            region TEXT,
            issued_at TIMESTAMPTZ,
            message_text TEXT,
            hazard_type TEXT,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            coordinate_count INTEGER,
            quality_score DOUBLE PRECISION,
            quality_flags JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (record_id)
        ) PARTITION BY HASH (record_id);
        """
    )
    for remainder in range(32):
        await pg.execute(
            f"""
            CREATE TABLE {CURRENT_PARENT_REPLACEMENT}_p{remainder:02d}
            PARTITION OF {CURRENT_PARENT_REPLACEMENT}
            FOR VALUES WITH (MODULUS 32, REMAINDER {remainder});
            """
        )


async def _create_history_replacement(pg) -> None:
    await pg.execute(
        f"""
        CREATE TABLE {HISTORY_PARENT_REPLACEMENT} (
            history_id BIGSERIAL,
            record_id TEXT NOT NULL,
            data_source TEXT NOT NULL,
            data_type TEXT NOT NULL,
            navarea_id INTEGER,
            warning_no TEXT,
            serial_number INTEGER,
            warning_year INTEGER,
            region TEXT,
            issued_at TIMESTAMPTZ,
            message_text TEXT,
            hazard_type TEXT,
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            coordinate_count INTEGER,
            quality_score DOUBLE PRECISION,
            quality_flags JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        ) PARTITION BY RANGE (issued_at);
        """
    )
    await pg.execute(
        f"""
        CREATE TABLE {HISTORY_PARENT_REPLACEMENT}_default
        PARTITION OF {HISTORY_PARENT_REPLACEMENT} DEFAULT;
        """
    )


async def _ensure_history_month_partitions(pg, source_table: str) -> None:
    rows = await pg.fetch_all(
        f"""
        SELECT DISTINCT date_trunc('month', issued_at) AS month_start
        FROM {source_table}
        WHERE issued_at IS NOT NULL
        ORDER BY month_start
        """
    )
    for row in rows:
        month_start = row["month_start"]
        suffix = month_start.strftime("%Y%m")
        month_start_literal = month_start.strftime("%Y-%m-%d")
        if month_start.month == 12:
            month_end = month_start.replace(year=month_start.year + 1, month=1, day=1)
        else:
            month_end = month_start.replace(month=month_start.month + 1, day=1)
        month_end_literal = month_end.strftime("%Y-%m-%d")
        await pg.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {HISTORY_PARENT_REPLACEMENT}_{suffix}
            PARTITION OF {HISTORY_PARENT_REPLACEMENT}
            FOR VALUES FROM ('{month_start_literal}') TO ('{month_end_literal}')
            """
        )


async def _rebuild_current_table(pg) -> None:
    if await _table_exists(pg, CURRENT_PARENT_REPLACEMENT):
        await pg.execute(f"DROP TABLE {CURRENT_PARENT_REPLACEMENT} CASCADE")
    await _create_current_replacement(pg)
    if await _table_exists(pg, CURRENT_TABLE):
        current_select = await _build_transform_select(pg, CURRENT_TABLE, include_history_id=False)
        await pg.execute(
            f"""
            INSERT INTO {CURRENT_PARENT_REPLACEMENT} (
                record_id, data_source, data_type, navarea_id, warning_no,
                serial_number, warning_year, region, issued_at, message_text,
                hazard_type, latitude, longitude, coordinate_count,
                quality_score, quality_flags, created_at, updated_at
            )
            {current_select}
            """
        )
        await pg.execute(f"DROP TABLE {CURRENT_TABLE} CASCADE")
    await pg.execute(f"ALTER TABLE {CURRENT_PARENT_REPLACEMENT} RENAME TO ods_navwarn")
    await pg.execute(
        "CREATE INDEX IF NOT EXISTS idx_ods_navwarn_data_source ON ts_ods.ods_navwarn (data_source)"
    )
    await pg.execute(
        "CREATE INDEX IF NOT EXISTS idx_ods_navwarn_issued_at ON ts_ods.ods_navwarn (issued_at DESC)"
    )
    await pg.execute(
        "CREATE INDEX IF NOT EXISTS idx_ods_navwarn_updated_at ON ts_ods.ods_navwarn (updated_at DESC)"
    )


async def _rebuild_history_table(pg) -> None:
    if await _table_exists(pg, HISTORY_PARENT_REPLACEMENT):
        await pg.execute(f"DROP TABLE {HISTORY_PARENT_REPLACEMENT} CASCADE")
    await _create_history_replacement(pg)
    if await _table_exists(pg, HISTORY_TABLE):
        await _ensure_history_month_partitions(pg, HISTORY_TABLE)
        history_select = await _build_transform_select(pg, HISTORY_TABLE, include_history_id=True)
        await pg.execute(
            f"""
            INSERT INTO {HISTORY_PARENT_REPLACEMENT} (
                history_id, record_id, data_source, data_type, navarea_id, warning_no,
                serial_number, warning_year, region, issued_at, message_text,
                hazard_type, latitude, longitude, coordinate_count,
                quality_score, quality_flags, created_at, updated_at
            )
            {history_select}
            """
        )
        await pg.execute(f"DROP TABLE {HISTORY_TABLE} CASCADE")
    await pg.execute(f"ALTER TABLE {HISTORY_PARENT_REPLACEMENT} RENAME TO ods_navwarn")
    await pg.execute(
        "CREATE INDEX IF NOT EXISTS idx_ods_navwarn_record_id ON ts_ods_hist.ods_navwarn (record_id)"
    )
    await pg.execute(
        "CREATE INDEX IF NOT EXISTS idx_ods_navwarn_issued_at ON ts_ods_hist.ods_navwarn (issued_at DESC NULLS LAST)"
    )


async def _migrate_current_in_place(pg) -> None:
    await pg.execute(f"ALTER TABLE {CURRENT_TABLE} ADD COLUMN IF NOT EXISTS region TEXT")
    await pg.execute(f"ALTER TABLE {CURRENT_TABLE} ADD COLUMN IF NOT EXISTS latitude DOUBLE PRECISION")
    await pg.execute(f"ALTER TABLE {CURRENT_TABLE} ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION")
    await pg.execute(f"ALTER TABLE {CURRENT_TABLE} ADD COLUMN IF NOT EXISTS coordinate_count INTEGER")
    has_sea_name = await _column_exists(pg, CURRENT_TABLE, "sea_name")
    has_coordinates = await _column_exists(pg, CURRENT_TABLE, "coordinates")
    region_expr = "COALESCE(NULLIF(BTRIM(region), ''), NULLIF(BTRIM(sea_name), ''))"
    if not has_sea_name:
        region_expr = "NULLIF(BTRIM(region), '')"
    coordinate_count_expr = "coordinate_count"
    latitude_expr = "latitude"
    longitude_expr = "longitude"
    if has_coordinates:
        coordinate_count_expr = """
        COALESCE(
            coordinate_count,
            CASE
                WHEN coordinates IS NULL OR jsonb_typeof(coordinates) <> 'array' THEN 0
                ELSE jsonb_array_length(coordinates)
            END
        )
        """
        latitude_expr = """
        COALESCE(
            latitude,
            NULLIF(coordinates->0->>'lat', '')::double precision
        )
        """
        longitude_expr = """
        COALESCE(
            longitude,
            NULLIF(coordinates->0->>'lon', '')::double precision
        )
        """
    await pg.execute(
        f"""
        UPDATE {CURRENT_TABLE}
        SET
            region = {region_expr},
            coordinate_count = {coordinate_count_expr},
            latitude = {latitude_expr},
            longitude = {longitude_expr}
        """
    )
    if await _column_exists(pg, CURRENT_TABLE, "warning_prefix"):
        await pg.execute(f"ALTER TABLE {CURRENT_TABLE} DROP COLUMN warning_prefix")
    if await _column_exists(pg, CURRENT_TABLE, "sea_name"):
        await pg.execute(f"ALTER TABLE {CURRENT_TABLE} DROP COLUMN sea_name")
    if await _column_exists(pg, CURRENT_TABLE, "coordinates"):
        await pg.execute(f"ALTER TABLE {CURRENT_TABLE} DROP COLUMN coordinates")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    pg = get_pg_client()
    await pg.connect()
    await ensure_ddlregistry_table(pg)

    if await _table_exists(pg, CURRENT_TABLE):
        if await _is_partitioned(pg, CURRENT_TABLE):
            await _migrate_current_in_place(pg)
        else:
            await _rebuild_current_table(pg)
    else:
        await _rebuild_current_table(pg)

    await _rebuild_history_table(pg)
    logger.info("ODS navwarn schema migration completed")
    await pg.close()


if __name__ == "__main__":
    asyncio.run(main())
