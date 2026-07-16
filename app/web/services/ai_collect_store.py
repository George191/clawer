from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from app.config.settings import settings
from app.storage.postgres_client import get_pg_client


_DDL = """
CREATE TABLE IF NOT EXISTS public.ai_collect_templates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    version text NOT NULL DEFAULT 'v1.0',
    title text NOT NULL,
    domain text NOT NULL DEFAULT '',
    favicon_url text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('active', 'draft', 'deprecated')),
    yaml_content text NOT NULL,
    adapter text NOT NULL DEFAULT '',
    description text NOT NULL DEFAULT '',
    output_tag text NOT NULL DEFAULT '',
    owner text NOT NULL DEFAULT 'AI Collect',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);
ALTER TABLE public.ai_collect_templates
    ADD COLUMN IF NOT EXISTS favicon_url text NOT NULL DEFAULT '';
UPDATE public.ai_collect_templates
SET favicon_url = 'https://' || domain || '/favicon.ico'
WHERE favicon_url = '' AND domain ~ '^[A-Za-z0-9.-]+[.][A-Za-z]{2,}$';
CREATE TABLE IF NOT EXISTS public.ai_collect_tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    template_name text NOT NULL,
    template_version text NOT NULL DEFAULT 'v1.0',
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'failed', 'paused')),
    progress integer NOT NULL DEFAULT 0,
    records bigint NOT NULL DEFAULT 0,
    throughput integer NOT NULL DEFAULT 0,
    control_state text,
    download_state text NOT NULL DEFAULT 'idle',
    sync_state text NOT NULL DEFAULT 'idle',
    schedule jsonb NOT NULL DEFAULT '{}'::jsonb,
    parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    policies jsonb NOT NULL DEFAULT '{}'::jsonb,
    owner text NOT NULL DEFAULT 'AI Collect',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz
);
CREATE TABLE IF NOT EXISTS public.ai_collect_task_logs (
    id bigserial PRIMARY KEY,
    task_id uuid NOT NULL REFERENCES public.ai_collect_tasks(id) ON DELETE CASCADE,
    level text NOT NULL DEFAULT 'info',
    message text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.ai_collect_analyses (
    template_id text PRIMARY KEY,
    source_url text NOT NULL,
    template_name text NOT NULL,
    template_yaml text NOT NULL,
    adapter_code text NOT NULL DEFAULT '',
    fields jsonb NOT NULL DEFAULT '[]'::jsonb,
    pagination jsonb NOT NULL DEFAULT '{}'::jsonb,
    sample_items jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ai_collect_templates_status ON public.ai_collect_templates(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_collect_tasks_status ON public.ai_collect_tasks(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_collect_task_logs_task ON public.ai_collect_task_logs(task_id, created_at DESC);
"""


class AICollectStore:
    def __init__(self) -> None:
        self._pg = get_pg_client()
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self._pg.init_schema([_DDL])
        await self._import_local_templates()
        self._initialized = True

    async def _import_local_templates(self) -> None:
        template_dir = Path(settings.template_dir)
        if not template_dir.exists():
            return
        for path in sorted([*template_dir.glob("*.yaml"), *template_dir.glob("*.yml")]):
            try:
                content = path.read_text(encoding="utf-8")
                raw = yaml.safe_load(content)
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("name") or path.stem)
                version = str(raw.get("version") or "v1.0")
                title = str(raw.get("display_name") or name.replace("_", " ").title())
                base_url = str(raw.get("base_url") or "")
                domain = urlparse(base_url).hostname or base_url
                parsed_base_url = urlparse(base_url)
                favicon_url = (
                    f"{parsed_base_url.scheme}://{parsed_base_url.netloc}/favicon.ico"
                    if parsed_base_url.scheme and parsed_base_url.netloc
                    else ""
                )
                adapter_path = Path("app/adapters") / f"{name}.py"
                await self._pg.execute(
                    """
                    INSERT INTO public.ai_collect_templates
                        (name, version, title, domain, favicon_url, status, yaml_content, adapter, description, output_tag)
                    VALUES
                        (:name, :version, :title, :domain, :favicon_url, 'active', :yaml, :adapter, :description, :output_tag)
                    ON CONFLICT (name, version) DO NOTHING
                    """,
                    {
                        "name": name,
                        "version": version,
                        "title": title,
                        "domain": domain,
                        "favicon_url": favicon_url,
                        "yaml": content,
                        "adapter": adapter_path.as_posix() if adapter_path.exists() else "",
                        "description": str(raw.get("description") or ""),
                        "output_tag": str(raw.get("data_type") or "other"),
                    },
                )
            except Exception:
                continue

    async def save_analysis(self, payload: dict[str, Any]) -> None:
        await self.initialize()
        await self._pg.execute(
            """
            INSERT INTO public.ai_collect_analyses
                (template_id, source_url, template_name, template_yaml, adapter_code, fields, pagination, sample_items)
            VALUES
                (:template_id, :source_url, :template_name, :template_yaml, :adapter_code,
                 CAST(:fields AS jsonb), CAST(:pagination AS jsonb), CAST(:sample_items AS jsonb))
            ON CONFLICT (template_id) DO UPDATE SET
                source_url = EXCLUDED.source_url,
                template_name = EXCLUDED.template_name,
                template_yaml = EXCLUDED.template_yaml,
                adapter_code = EXCLUDED.adapter_code,
                fields = EXCLUDED.fields,
                pagination = EXCLUDED.pagination,
                sample_items = EXCLUDED.sample_items,
                created_at = now()
            """,
            {
                **payload,
                "fields": json.dumps(payload.get("fields", []), ensure_ascii=False),
                "pagination": json.dumps(payload.get("pagination", {}), ensure_ascii=False),
                "sample_items": json.dumps(payload.get("sample_items", []), ensure_ascii=False),
            },
        )

    async def get_analysis(self, template_id: str) -> dict[str, Any] | None:
        await self.initialize()
        return await self._pg.fetch_one(
            "SELECT * FROM public.ai_collect_analyses WHERE template_id = :template_id",
            {"template_id": template_id},
        )

    async def list_templates(self) -> list[dict[str, Any]]:
        await self.initialize()
        return await self._pg.fetch_all(
            """
            SELECT t.*,
                   COALESCE((SELECT count(*) FROM public.ai_collect_tasks task
                             WHERE task.template_name = t.name), 0)::int AS task_count
            FROM public.ai_collect_templates t
            ORDER BY t.updated_at DESC
            """
        )

    async def update_template(self, template_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        await self.initialize()
        return await self._pg.fetch_one(
            """
            UPDATE public.ai_collect_templates SET
                yaml_content = :yaml_content,
                adapter = :adapter,
                description = :description,
                output_tag = :output_tag,
                updated_at = now()
            WHERE id = CAST(:id AS uuid)
            RETURNING *
            """,
            {"id": template_id, **payload},
        )

    async def release_template(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self.initialize()
        row = await self._pg.fetch_one(
            """
            INSERT INTO public.ai_collect_templates
                (name, version, title, domain, favicon_url, status, yaml_content, adapter, description, output_tag, metadata)
            VALUES
                (:name, :version, :title, :domain, :favicon_url, :status, :yaml_content, :adapter,
                 :description, :output_tag, CAST(:metadata AS jsonb))
            ON CONFLICT (name, version) DO UPDATE SET
                title = EXCLUDED.title,
                domain = EXCLUDED.domain,
                favicon_url = EXCLUDED.favicon_url,
                status = EXCLUDED.status,
                yaml_content = EXCLUDED.yaml_content,
                adapter = EXCLUDED.adapter,
                description = EXCLUDED.description,
                output_tag = EXCLUDED.output_tag,
                metadata = EXCLUDED.metadata,
                updated_at = now()
            RETURNING *
            """,
            {**payload, "metadata": json.dumps(payload.get("metadata", {}), ensure_ascii=False)},
        )
        if row is None:
            raise RuntimeError("Template release did not return a row")
        return row

    async def list_tasks(self) -> list[dict[str, Any]]:
        await self.initialize()
        tasks = await self._pg.fetch_all(
            "SELECT * FROM public.ai_collect_tasks ORDER BY updated_at DESC"
        )
        for task in tasks:
            task["logs"] = await self._pg.fetch_all(
                """
                SELECT level, message, created_at FROM public.ai_collect_task_logs
                WHERE task_id = :task_id ORDER BY created_at DESC LIMIT 100
                """,
                {"task_id": task["id"]},
            )
        return tasks

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self.initialize()
        task = await self._pg.fetch_one(
            """
            INSERT INTO public.ai_collect_tasks
                (name, template_name, template_version, status, schedule, parameters, policies, owner, started_at)
            VALUES
                (:name, :template_name, :template_version, 'queued', CAST(:schedule AS jsonb),
                 CAST(:parameters AS jsonb), CAST(:policies AS jsonb), :owner, now())
            RETURNING *
            """,
            {
                **payload,
                "schedule": json.dumps(payload.get("schedule", {}), ensure_ascii=False),
                "parameters": json.dumps(payload.get("parameters", {}), ensure_ascii=False),
                "policies": json.dumps(payload.get("policies", {}), ensure_ascii=False),
            },
        )
        if task is None:
            raise RuntimeError("Task creation did not return a row")
        await self.add_task_log(str(task["id"]), "info", "task created and queued")
        task["logs"] = await self._pg.fetch_all(
            "SELECT level, message, created_at FROM public.ai_collect_task_logs WHERE task_id = :task_id ORDER BY created_at DESC",
            {"task_id": task["id"]},
        )
        return task

    async def update_task(self, task_id: str, changes: dict[str, Any], level: str, message: str) -> dict[str, Any] | None:
        await self.initialize()
        task = await self._pg.fetch_one(
            """
            UPDATE public.ai_collect_tasks SET
                status = COALESCE(:status, status),
                control_state = COALESCE(:control_state, control_state),
                download_state = COALESCE(:download_state, download_state),
                sync_state = COALESCE(:sync_state, sync_state),
                updated_at = now()
            WHERE id = CAST(:id AS uuid)
            RETURNING *
            """,
            {"id": task_id, **changes},
        )
        if task:
            await self.add_task_log(task_id, level, message)
            task["logs"] = await self._pg.fetch_all(
                "SELECT level, message, created_at FROM public.ai_collect_task_logs WHERE task_id = :task_id ORDER BY created_at DESC LIMIT 100",
                {"task_id": task_id},
            )
        return task

    async def add_task_log(self, task_id: str, level: str, message: str) -> None:
        await self._pg.execute(
            """
            INSERT INTO public.ai_collect_task_logs (task_id, level, message)
            VALUES (CAST(:task_id AS uuid), :level, :message)
            """,
            {"task_id": task_id, "level": level, "message": message},
        )


ai_collect_store = AICollectStore()
