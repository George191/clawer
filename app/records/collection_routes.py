"""Resolve template collection aliases, preserving ownership even while disabled."""

from __future__ import annotations

from app.storage.postgres_client import get_pg_client


def canonical_collection_name(template_name: str, data_type: str) -> str:
    """Name a physical collection as <data_type>_<website or endpoint>."""
    if not template_name or not data_type:
        raise ValueError("Collection routes require a template name and data type")
    if template_name.startswith(f"{data_type}_"):
        return template_name
    suffix = f"_{data_type}"
    source = template_name[:-len(suffix)] if template_name.endswith(suffix) else template_name
    return f"{data_type}_{source}"


class CollectionRouteStore:
    def __init__(self) -> None:
        self._physical_names: set[str] = set()

    def _remember(self, route: dict[str, str]) -> dict[str, str]:
        expected = canonical_collection_name(route["template_name"], route["data_type"])
        if route["collection_name"] != expected:
            raise ValueError(
                f"Collection {route['collection_name']!r} must be migrated to {expected!r}"
            )
        self._physical_names.add(route["collection_name"])
        return route

    async def resolve_for_write(
        self, template_name: str, data_type: str | None,
    ) -> dict[str, str]:
        """Resolve a write target; enabled only controls downstream consumption."""
        routes = await get_pg_client().fetch_all(
            """
            SELECT template_name, collection_name, data_type, enabled
            FROM public.ai_collect_template_alias AS routes
            WHERE template_name = :template_name OR collection_name = :template_name
            """,
            {"template_name": template_name},
        )
        if not routes:
            template = await get_pg_client().fetch_one(
                """
                SELECT data_type FROM public.ai_collect_templates
                WHERE name = :name
                ORDER BY (status = 'active') DESC, updated_at DESC LIMIT 1
                """,
                {"name": template_name},
            )
            if template is None:
                raise ValueError(f"Unknown collection or template: {template_name!r}")
            if data_type is not None and data_type != template["data_type"]:
                raise ValueError(f"No collection route for {template_name!r} with type {data_type!r}")
            data_type = template["data_type"]
            return self._remember({
                "template_name": template_name,
                "collection_name": canonical_collection_name(template_name, data_type),
                "data_type": data_type,
            })
        matches = [route for route in routes if template_name == route["collection_name"]
                   and (data_type is None or route["data_type"] == data_type)]
        if not matches:
            matches = [route for route in routes if data_type is None or route["data_type"] == data_type]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one collection route for {template_name!r} with type {data_type!r}; "
                f"found {len(matches)}"
            )
        return self._remember(matches[0])

    async def resolve_collection_name(self, name: str, data_type: str | None = None) -> str:
        if name in self._physical_names:
            return name
        return (await self.resolve_for_write(name, data_type))["collection_name"]

    async def list_by_data_type(
        self, data_type: str | None, template_name: str | None = None,
    ) -> list[dict[str, str]]:
        routes = await get_pg_client().fetch_all(
            """
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
                LEFT JOIN public.ai_collect_template_alias AS routes
                  ON routes.template_name = templates.name
                WHERE routes.enabled OR (
                    routes.template_name IS NULL
                    AND NOT EXISTS (
                        SELECT 1
                        FROM public.ai_collect_template_alias AS collection_owner
                        WHERE collection_owner.collection_name = templates.name
                          AND collection_owner.data_type = templates.data_type
                    )
                )
            )
            SELECT DISTINCT ON (collection_name) template_name, collection_name, data_type,
                   explicit_route
            FROM resolved_collections
            ORDER BY collection_name, explicit_route DESC, template_name
            """,
        )
        result = []
        for route in routes:
            route = dict(route)
            if not route.pop("explicit_route"):
                route["collection_name"] = canonical_collection_name(
                    route["template_name"], route["data_type"],
                )
            if data_type is not None and route["data_type"] != data_type:
                continue
            if template_name is not None and template_name not in (
                route["template_name"], route["collection_name"],
            ):
                continue
            result.append(self._remember(route))
        return result


collection_route_store = CollectionRouteStore()
