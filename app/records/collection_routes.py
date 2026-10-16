"""Resolve collection routes, preserving explicit ownership even while disabled."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.storage.postgres_client import get_pg_client


class CollectionRouteStore:
    async def resolve_for_write(
        self, template_name: str, data_type: str,
    ) -> dict[str, str]:
        """Resolve a write target; enabled only controls downstream consumption."""
        routes = await get_pg_client().fetch_all(
            """
            SELECT template_name, collection_name, data_type
            FROM public.syncer_collections
            WHERE template_name = :template_name OR collection_name = :template_name
            """,
            {"template_name": template_name},
        )
        if not routes:
            return {
                "template_name": template_name,
                "collection_name": template_name,
                "data_type": data_type,
            }
        matches = [route for route in routes if route["collection_name"] == template_name]
        if not matches:
            matches = [route for route in routes if route["data_type"] == data_type]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one collection route for {template_name!r} with type {data_type!r}; "
                f"found {len(matches)}"
            )
        return matches[0]

    async def list_by_data_type(
        self, data_type: str | None, template_name: str | None = None,
    ) -> list[dict[str, str]]:
        params = {}
        filters = []
        if data_type is not None:
            params["data_type"] = data_type
            filters.append("data_type = :data_type")
        if template_name is not None:
            params["template_name"] = template_name
            filters.append("(template_name = :template_name OR collection_name = :template_name)")
        where_clause = "WHERE " + " AND ".join(filters) if filters else ""
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
            {where_clause}
            ORDER BY collection_name, explicit_route DESC, template_name
            """,
            params,
        )


collection_route_store = CollectionRouteStore()

