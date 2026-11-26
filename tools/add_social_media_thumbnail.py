"""Add the downloaded Twitter thumbnail column before deploying ODS workers."""
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text

from app.etl.schema.catalogs import current_table_ddl
from app.etl.schema.registry import object_table_ref, require_registered_object, save_registered_ddl
from app.storage.business_database import get_layer_pg_client
from app.storage.postgres_client import get_pg_client


async def main():
    pg = get_pg_client()
    warehouse = get_layer_pg_client('ods')
    await pg.connect(max_retries=1)
    try:
        row = await require_registered_object(pg, 'ods', 'social_media')
        relation = object_table_ref(row)
        location = {key: row[key] for key in ('schema_name', 'table_name')}
        async with warehouse.session() as session:
            await session.execute(text("SET LOCAL lock_timeout='5s'"))
            await session.execute(text("SET LOCAL statement_timeout='45s'"))
            await session.execute(text(
                f'ALTER TABLE {relation} ADD COLUMN IF NOT EXISTS thumbnail TEXT'
            ))
            await session.commit()
        await save_registered_ddl(
            pg, layer='ods', table='social_media',
            ddl_sql=current_table_ddl('ods', 'social_media', **location), allow_update=True,
            catalog_pg=warehouse,
            updated_by='maintenance:add-social-media-thumbnail',
        )
        column = await warehouse.fetch_one("""SELECT data_type FROM information_schema.columns
            WHERE table_schema=:schema_name AND table_name=:table_name
            AND column_name='thumbnail'""", location)
        assert column and column['data_type'] == 'text'
        print(json.dumps({'thumbnail_column_verified': True}))
    finally:
        await warehouse.close()
        await pg.close()


if __name__ == '__main__':
    asyncio.run(main())
