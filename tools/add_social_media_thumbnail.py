"""Add the downloaded Twitter thumbnail column before deploying ODS workers."""
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text

from app.etl.schema.catalogs import current_table_ddl
from app.etl.schema.registry import save_registered_ddl
from app.storage.postgres_client import get_pg_client


async def main():
    pg = get_pg_client()
    await pg.connect(max_retries=1)
    try:
        async with pg.session() as session:
            await session.execute(text("SET LOCAL lock_timeout='5s'"))
            await session.execute(text("SET LOCAL statement_timeout='45s'"))
            await session.execute(text(
                'ALTER TABLE ts_ods.ods_social_media ADD COLUMN IF NOT EXISTS thumbnail TEXT'
            ))
            await session.commit()
        await save_registered_ddl(
            pg, layer='ods', table='social_media',
            ddl_sql=current_table_ddl('ods', 'social_media'), allow_update=True,
            updated_by='maintenance:add-social-media-thumbnail',
        )
        column = await pg.fetch_one("""SELECT data_type FROM information_schema.columns
            WHERE table_schema='ts_ods' AND table_name='ods_social_media'
            AND column_name='thumbnail'""")
        assert column and column['data_type'] == 'text'
        print(json.dumps({'thumbnail_column_verified': True}))
    finally:
        await pg.close()


if __name__ == '__main__':
    asyncio.run(main())
