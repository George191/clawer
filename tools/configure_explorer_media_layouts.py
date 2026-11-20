"""Initialize explicit Explorer media modes; preserve column order, sizing and visibility."""
from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.etl.schema.catalogs import _CATALOG_FIELDS
from app.web.api.models.explorer_layouts import ExplorerResultLayout
from app.web.api.routes.explorer_layouts import reconcile
from app.web.core.db import engine
from app.web.services.etl_layers import get_etl_layers


MEDIA = {
    'acticle': {'cover', 'content_html', 'images'},
    'news': {'content_html', 'summary_html', 'images', 'videos', 'audios', 'attachments', 'slides', 'thumbnail'},
    'tender': {'attachments'},
    'patent': {'url', 'thumbnail', 'figures'},
    'filing_document': {'url'},
    'intelligence': {'url'},
    'social_account': {'avatar_url'},
    'social_media': {'url', 'thumbnail'},
    'search_result': {'card_html'},
}


def configure(layout, fields, media):
    result = reconcile(layout or {'columns': [], 'sorts': [], 'wrap': False}, fields)
    for column in result['columns']:
        column['presentation'] = 'media' if column['field'] in media else 'value'
    return result


async def inventory():
    assets = {}
    async with get_etl_layers().session() as session:
        rows = (await session.execute(text("""SELECT n.nspname AS schema, c.relname AS name, a.attname AS field,
            format_type(a.atttypid,a.atttypmod) AS type FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid
            WHERE c.relkind IN ('r','p') AND NOT c.relispartition AND a.attnum>0 AND NOT a.attisdropped
            AND n.nspname IN ('ts_ods','ts_task','ts_dwd','ts_dws','ts_dim','ts_ads') ORDER BY n.nspname,c.relname,a.attnum"""))).mappings()
        for row in rows:
            kind = row['name'].split('_', 1)[-1]
            if kind in _CATALOG_FIELDS:
                key = (row['schema'], row['name'], kind)
                assets.setdefault(key, {})[row['field']] = row['type']
    return [(*key, fields) for key, fields in assets.items()]


async def migrate(assets, apply):
    model = ExplorerResultLayout
    result = []
    for schema, table, kind, fields in assets:
        async with AsyncSession(engine) as session, session.begin():
            rows = list((await session.scalars(select(model).where(model.schema_name == schema, model.table_name == table).with_for_update())).all())
            if not any(row.owner_id is None for row in rows):
                statement = insert(model).values(id=uuid4(), schema_name=schema, table_name=table, owner_id=None,
                                                version=uuid4(), layout=configure(None, fields, MEDIA.get(kind, set()))).on_conflict_do_nothing()
                if apply:
                    await session.execute(statement)
            changed = 0
            for row in rows:
                if row.owner_id is not None and row.layout is None:
                    continue
                layout = configure(row.layout, fields, MEDIA.get(kind, set()))
                if row.layout != layout:
                    changed += 1
                    if apply:
                        row.layout, row.version = layout, uuid4()
            result.append({'schema': schema, 'table': table, 'media': sorted(MEDIA.get(kind, set()) & fields.keys()), 'updatedSlots': changed})
    await engine.dispose()
    return result


def worker(args):
    return asyncio.run(migrate(*args))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    assets = asyncio.run(inventory())
    # Fixed disjoint asset ranges; each transaction handles a single table's layout slots.
    with multiprocessing.get_context('spawn').Pool(6) as pool:
        results = pool.map(worker, [(assets[n::6], args.apply) for n in range(6)])
    print(json.dumps({'applied': args.apply, 'assets': [item for batch in results for item in batch]}, sort_keys=True))
