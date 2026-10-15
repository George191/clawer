"""Resolve collection routes, preserving explicit ownership even while disabled."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.storage.postgres_client import get_pg_client


class SyncerCollectionStore:
    async def list_by_data_type(
        self, data_type: str, template_name: str | None = None,
    ) -> list[dict[str, str]]:
        params = {"data_type": data_type}
        template_filter = ""
        if template_name is not None:
            params["template_name"] = template_name
            template_filter = """
                AND (template_name = :template_name OR collection_name = :template_name)
            """
        return await get_pg_client().fetch_all(
            f"""
            WITH released_templates AS (
                SELECT DISTINCT ON (name) name, data_type
                FROM public.ai_collect_templates
                ORDER BY name, (status = 'active') DESC, updated_at DESC
            ), template_groups AS (
                SELECT data_type, array_agg(name ORDER BY name) AS names
                FROM released_templates
                GROUP BY data_type
            ), templates AS (
                SELECT data_type, unnest(names) AS name
                FROM template_groups
            ), resolved_collections AS (
                SELECT templates.name AS template_name,
                       COALESCE(routes.collection_name, templates.name) AS collection_name,
                       COALESCE(routes.data_type, templates.data_type) AS data_type,
                       (routes.template_name IS NOT NULL) AS explicit_route
                FROM templates
                LEFT JOIN public.syncer_collections AS routes
                  ON routes.template_name = templates.name
                WHERE routes.enabled OR (
                    routes.template_name IS NULL
                    AND NOT EXISTS (
                        SELECT 1
                        FROM public.syncer_collections AS collection_owner
                        WHERE collection_owner.collection_name = templates.name
                          AND collection_owner.data_type = templates.data_type
                    )
                )
            )
            SELECT DISTINCT ON (collection_name) template_name, collection_name, data_type
            FROM resolved_collections
            WHERE data_type = :data_type
            {template_filter}
            ORDER BY collection_name, explicit_route DESC, template_name
            """,
            params,
        )


syncer_collection_store = SyncerCollectionStore()


async def _init_schema() -> None:
    sql = (Path(__file__).parent / "sql" / "init_syncer_collections.sql").read_text(
        encoding="utf-8",
    )
    pg = get_pg_client()
    try:
        await pg.init_schema([sql])
    finally:
        await pg.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init", action="store_true", required=True, help="Initialize collection routes")
    parser.parse_args()
    asyncio.run(_init_schema())
