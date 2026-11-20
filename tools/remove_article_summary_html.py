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
from app.etl.schema.registry import save_registered_ddl
from app.storage.postgres_client import get_pg_client


async def main():
    assert 'summary_html' not in CATALOG_ODS_COLUMNS['acticle']
    assert 'summary_html' not in normalize_acticle({'summary': 'verified'})
    assert 'summary_html' in CATALOG_ODS_COLUMNS['news']
    pg = get_pg_client()
    await pg.connect(max_retries=1)
    try:
        async with pg.session() as session:
            await session.execute(text("SET LOCAL lock_timeout='5s'"))
            await session.execute(text("SET LOCAL statement_timeout='45s'"))
            # RESTRICT is deliberate: a dependent database object must be reviewed.
            await session.execute(text('ALTER TABLE ts_ods.ods_acticle DROP COLUMN IF EXISTS summary_html RESTRICT'))
            rows = (await session.execute(text("""SELECT source_key,settings FROM public.explorer_property_profiles
                WHERE schema_name='ts_ods' AND table_name='ods_acticle' FOR UPDATE"""))).mappings().all()
            for row in rows:
                settings = dict(row['settings'])
                if row['source_key'] == 'physical':
                    settings['properties'] = [p for p in settings.get('properties', []) if p.get('sourceColumn') != 'summary_html']
                    settings['knownColumns'] = [c for c in settings.get('knownColumns', []) if c != 'summary_html']
                    await session.execute(text("""UPDATE public.explorer_property_profiles SET settings=CAST(:settings AS jsonb),version=:version
                        WHERE schema_name='ts_ods' AND table_name='ods_acticle' AND source_key=:key"""),
                        {'settings': json.dumps(settings), 'version': uuid4(), 'key': row['source_key']})
            await session.commit()
        await save_registered_ddl(pg, 'ods', 'acticle', current_table_ddl('ods', 'acticle'),
                                  allow_update=True, updated_by='maintenance:remove-derived-article-summary')
        rows = await pg.fetch_all("""SELECT table_schema,table_name FROM information_schema.columns
            WHERE column_name='summary_html' AND table_name IN ('ods_acticle','ods_news')""")
        assert not any(row['table_name'] == 'ods_acticle' for row in rows)
        assert any(row['table_name'] == 'ods_news' for row in rows)
        print(json.dumps({'article_column_removed': True, 'news_column_preserved': True,
                          'ddl_and_property_metadata_updated': True}), flush=True)
    finally:
        await pg.close()


if __name__ == '__main__':
    asyncio.run(main())
