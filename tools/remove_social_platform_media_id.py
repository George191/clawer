"""Remove the redundant social media column after deploying corrected ODS workers."""
import asyncio
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text

from app.etl.schema.catalogs import CATALOG_ODS_COLUMNS, current_table_ddl
from app.etl.schema.registry import object_table_ref, require_registered_object, save_registered_ddl
from app.storage.business_database import get_layer_pg_client
from app.storage.postgres_client import get_pg_client


async def main():
    assert 'platform_media_id' not in CATALOG_ODS_COLUMNS['social_media']
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
            columns = (await session.execute(text("""SELECT column_name FROM information_schema.columns
                WHERE table_schema=:schema_name AND table_name=:table_name"""), location)).scalars().all()
            assert 'source_url' in columns and 'url' in columns
            if 'platform_media_id' in columns:
                different = (await session.execute(text(f"""SELECT count(*) FROM {relation}
                    WHERE platform_media_id IS DISTINCT FROM source_url"""))).scalar_one()
                assert different == 0, 'Media IDs differ from source URLs; review before deleting'
                await session.execute(text(f'ALTER TABLE {relation} DROP COLUMN platform_media_id RESTRICT'))
        async with pg.session() as session:
            rows = (await session.execute(text("""SELECT source_key,settings FROM public.explorer_property_profiles
                WHERE schema_name=:schema_name AND table_name=:table_name FOR UPDATE"""), location)).mappings().all()
            for row in rows:
                settings = dict(row['settings'])
                if row['source_key'] == 'physical':
                    settings['properties'] = [p for p in settings.get('properties', []) if p.get('sourceColumn') != 'platform_media_id']
                    settings['knownColumns'] = [c for c in settings.get('knownColumns', []) if c != 'platform_media_id']
                    await session.execute(text("""UPDATE public.explorer_property_profiles SET settings=CAST(:settings AS jsonb),version=:version
                        WHERE schema_name=:schema_name AND table_name=:table_name AND source_key=:key"""),
                        {**location, 'settings': json.dumps(settings), 'version': uuid4(), 'key': row['source_key']})
            await session.commit()
        await save_registered_ddl(pg, layer='ods', table='social_media', catalog_pg=warehouse,
                                  ddl_sql=current_table_ddl('ods', 'social_media', **location),
                                  allow_update=True, updated_by='maintenance:remove-redundant-social-media-id')
        columns = await warehouse.fetch_all("""SELECT column_name FROM information_schema.columns
            WHERE table_schema=:schema_name AND table_name=:table_name""", location)
        assert 'platform_media_id' not in {r['column_name'] for r in columns}
        print(json.dumps({'column_removed': True, 'ddl_and_property_metadata_updated': True}), flush=True)
    finally:
        await warehouse.close()
        await pg.close()


if __name__ == '__main__':
    asyncio.run(main())
