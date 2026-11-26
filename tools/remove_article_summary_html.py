"""Remove the derived article summary column after deploying the corrected ODS workers."""
import asyncio
import json
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text

from app.etl.normalizers.acticle import normalize_acticle
from app.etl.schema.catalogs import CATALOG_ODS_COLUMNS, current_table_ddl
from app.etl.schema.registry import object_table_ref, require_registered_object, save_registered_ddl
from app.storage.business_database import get_layer_pg_client
from app.storage.postgres_client import get_pg_client


async def main():
    assert 'summary_html' not in CATALOG_ODS_COLUMNS['acticle']
    assert 'summary_html' not in normalize_acticle({'summary': 'verified'})
    assert 'summary_html' in CATALOG_ODS_COLUMNS['news']
    pg = get_pg_client()
    warehouse = get_layer_pg_client('ods')
    await pg.connect(max_retries=1)
    try:
        article = await require_registered_object(pg, 'ods', 'acticle')
        news = await require_registered_object(pg, 'ods', 'news')
        relation = object_table_ref(article)
        location = {key: article[key] for key in ('schema_name', 'table_name')}
        async with warehouse.session() as session:
            await session.execute(text("SET LOCAL lock_timeout='5s'"))
            await session.execute(text("SET LOCAL statement_timeout='45s'"))
            # RESTRICT requires reviewing any dependent database object.
            await session.execute(text(f'ALTER TABLE {relation} DROP COLUMN IF EXISTS summary_html RESTRICT'))
        async with pg.session() as session:
            await session.execute(text("SET LOCAL lock_timeout='5s'"))
            await session.execute(text("SET LOCAL statement_timeout='45s'"))
            rows = (await session.execute(text("""SELECT source_key,settings FROM public.explorer_property_profiles
                WHERE schema_name=:schema_name AND table_name=:table_name FOR UPDATE"""), location)).mappings().all()
            for row in rows:
                settings = dict(row['settings'])
                if row['source_key'] == 'physical':
                    settings['properties'] = [p for p in settings.get('properties', []) if p.get('sourceColumn') != 'summary_html']
                    settings['knownColumns'] = [c for c in settings.get('knownColumns', []) if c != 'summary_html']
                    await session.execute(text("""UPDATE public.explorer_property_profiles SET settings=CAST(:settings AS jsonb),version=:version
                        WHERE schema_name=:schema_name AND table_name=:table_name AND source_key=:key"""),
                        {**location, 'settings': json.dumps(settings), 'version': uuid4(), 'key': row['source_key']})
            await session.commit()
        await save_registered_ddl(pg, layer='ods', table='acticle', catalog_pg=warehouse,
                                  ddl_sql=current_table_ddl('ods', 'acticle', **location),
                                  allow_update=True, updated_by='maintenance:remove-derived-article-summary')
        check = """SELECT 1 FROM information_schema.columns WHERE column_name='summary_html'
            AND table_schema=:schema_name AND table_name=:table_name"""
        assert not await warehouse.fetch_one(check, location)
        assert await warehouse.fetch_one(check, {key: news[key] for key in location})
        print(json.dumps({'article_column_removed': True, 'news_column_preserved': True,
                          'ddl_and_property_metadata_updated': True}), flush=True)
    finally:
        await warehouse.close()
        await pg.close()


if __name__ == '__main__':
    asyncio.run(main())
