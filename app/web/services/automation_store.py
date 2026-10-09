from __future__ import annotations

import json
from typing import Any

from app.storage.postgres_client import get_pg_client

_DDL = """
CREATE TABLE IF NOT EXISTS public.automation_workflows (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    product_domain TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    nodes JSONB NOT NULL DEFAULT '[]'::jsonb,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


class AutomationStore:
    def __init__(self) -> None:
        self._pg = get_pg_client()
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await self._pg.init_schema([_DDL])
            self._ready = True

    async def list_workflows(self) -> list[dict[str, Any]]:
        await self._ensure()
        return await self._pg.fetch_all("SELECT * FROM public.automation_workflows ORDER BY updated_at DESC")

    async def save_workflow(self, payload: dict[str, Any], name: str | None = None) -> dict[str, Any]:
        await self._ensure()
        if name:
            row = await self._pg.fetch_one(
                """UPDATE public.automation_workflows SET product_domain=:product_domain,
                description=:description, nodes=CAST(:nodes AS jsonb), enabled=:enabled, updated_at=now()
                WHERE name=:name RETURNING *""",
                {**payload, "name": name, "nodes": json.dumps(payload.get("nodes", []), ensure_ascii=False)},
            )
        else:
            row = await self._pg.fetch_one(
                """INSERT INTO public.automation_workflows
                (name, product_domain, description, nodes, enabled)
                VALUES (:name, :product_domain, :description, CAST(:nodes AS jsonb), :enabled)
                RETURNING *""",
                {**payload, "nodes": json.dumps(payload.get("nodes", []), ensure_ascii=False)},
            )
        if row is None:
            raise ValueError("工作流不存在")
        return row

    async def delete_workflow(self, name: str) -> bool:
        await self._ensure()
        row = await self._pg.fetch_one(
            "DELETE FROM public.automation_workflows WHERE name=:name RETURNING id",
            {"name": name},
        )
        return row is not None


automation_store = AutomationStore()
