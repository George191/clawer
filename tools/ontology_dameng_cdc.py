"""Install DMDRS binding storage after the optional Data Source registry exists."""

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.etl.schema.dameng_cdc import DAMENG_CDC_DDL, DAMENG_CDC_INSTALLED
from app.storage.postgres_client import get_pg_client


async def migrate(pg, *, apply=False):
    async with pg.session() as session:
        await session.execute(text("SET LOCAL lock_timeout = '3s'"))
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('public.ontology_data_connections_migration'))"))
        if not await session.scalar(text("SELECT to_regclass('public.ontology_data_connections') IS NOT NULL")):
            raise RuntimeError("Install the Data Source registry first")
        installed = bool(await session.scalar(text(DAMENG_CDC_INSTALLED)))
        if apply and not installed:
            await session.execute(text(DAMENG_CDC_DDL))
        return installed or apply


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    async def run():
        pg = get_pg_client()
        try:
            await pg.connect(max_retries=1)
            return await migrate(pg, apply=args.apply)
        finally:
            await pg.close()

    ready = asyncio.run(run())
    print("DMDRS binding storage ready" if ready else "DMDRS binding migration required")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
