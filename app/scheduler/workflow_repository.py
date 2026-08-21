"""Persistent workflow definitions used by the automation control plane."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.storage.postgres_client import PostgresClient, get_pg_client

TABLE_NAME = "public.automation_workflows"
_DDL_PATH = Path(__file__).resolve().parent / "sql" / "init_automation_workflows.sql"


@dataclass
class WorkflowDefinition:
    name: str
    product_domain: str
    description: str = ""
    nodes: list[dict[str, Any]] = field(default_factory=list)
    enabled: bool = True
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None


class WorkflowRepository:
    def __init__(self, pg: PostgresClient | None = None) -> None:
        self._pg = pg
        self._ready = False

    def _get_pg(self) -> PostgresClient:
        if self._pg is None:
            self._pg = get_pg_client()
        return self._pg

    async def ensure_table(self) -> None:
        if self._ready:
            return
        pg = self._get_pg()
        await pg.connect()
        await pg.init_schema([_DDL_PATH.read_text(encoding="utf-8")])
        self._ready = True

    async def list_all(self) -> list[WorkflowDefinition]:
        await self.ensure_table()
        rows = await self._get_pg().fetch_all(
            f"SELECT id, name, product_domain, description, nodes, enabled, created_at, updated_at "
            f"FROM {TABLE_NAME} ORDER BY name"
        )
        return [self._row(row) for row in rows]

    async def get_by_name(self, name: str) -> WorkflowDefinition | None:
        await self.ensure_table()
        row = await self._get_pg().fetch_one(
            f"SELECT id, name, product_domain, description, nodes, enabled, created_at, updated_at "
            f"FROM {TABLE_NAME} WHERE name = :name",
            {"name": name},
        )
        return self._row(row) if row else None

    async def create(self, definition: WorkflowDefinition) -> WorkflowDefinition:
        await self.ensure_table()
        if await self.get_by_name(definition.name):
            raise ValueError(f"工作流名称已存在: {definition.name}")
        await self._get_pg().execute(
            f"""INSERT INTO {TABLE_NAME} (name, product_domain, description, nodes, enabled)
            VALUES (:name, :product_domain, :description, CAST(:nodes AS jsonb), :enabled)""",
            {
                "name": definition.name,
                "product_domain": definition.product_domain,
                "description": definition.description,
                "nodes": json.dumps(definition.nodes, ensure_ascii=False),
                "enabled": definition.enabled,
            },
        )
        created = await self.get_by_name(definition.name)
        assert created is not None
        return created

    async def update(self, name: str, changes: dict[str, Any]) -> WorkflowDefinition | None:
        await self.ensure_table()
        allowed = {"product_domain", "description", "enabled"}
        parts: list[str] = []
        params: dict[str, Any] = {"name": name}
        for key, value in changes.items():
            if key in allowed:
                parts.append(f"{key} = :{key}")
                params[key] = value
            elif key == "nodes":
                parts.append("nodes = CAST(:nodes AS jsonb)")
                params["nodes"] = json.dumps(value, ensure_ascii=False)
        if not parts:
            raise ValueError("没有可更新的字段")
        parts.append("updated_at = now()")
        await self._get_pg().execute(
            f"UPDATE {TABLE_NAME} SET {', '.join(parts)} WHERE name = :name",
            params,
        )
        return await self.get_by_name(name)

    async def delete(self, name: str) -> bool:
        await self.ensure_table()
        existing = await self.get_by_name(name)
        if existing is None:
            return False
        await self._get_pg().execute(f"DELETE FROM {TABLE_NAME} WHERE name = :name", {"name": name})
        return True

    @staticmethod
    def _row(row: dict[str, Any]) -> WorkflowDefinition:
        raw_nodes = row.get("nodes") or []
        if isinstance(raw_nodes, str):
            raw_nodes = json.loads(raw_nodes)
        return WorkflowDefinition(
            id=row.get("id"),
            name=row["name"],
            product_domain=row["product_domain"],
            description=row.get("description") or "",
            nodes=list(raw_nodes),
            enabled=bool(row.get("enabled", True)),
            created_at=str(row["created_at"]) if row.get("created_at") else None,
            updated_at=str(row["updated_at"]) if row.get("updated_at") else None,
        )
