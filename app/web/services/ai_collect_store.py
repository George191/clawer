from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import yaml
from curl_cffi import requests as curl_requests
from lxml import html as lxml_html

from app.config.settings import settings
from app.storage.minio_client import get_business_metadata_minio_client
from app.storage.postgres_client import get_pg_client

_DDL = """
CREATE TABLE IF NOT EXISTS public.ai_collect_templates (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    version text NOT NULL DEFAULT 'v1.0',
    title text NOT NULL,
    domain text NOT NULL DEFAULT '',
    template text NOT NULL DEFAULT '',
    icon text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('active', 'draft', 'deprecated')),
    adapter text NOT NULL DEFAULT '',
    description text NOT NULL DEFAULT '',
    output_tag text NOT NULL DEFAULT '',
    owner text NOT NULL DEFAULT 'AI Collect',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);
ALTER TABLE public.ai_collect_templates ADD COLUMN IF NOT EXISTS template text NOT NULL DEFAULT '';
ALTER TABLE public.ai_collect_templates ADD COLUMN IF NOT EXISTS icon text NOT NULL DEFAULT '';
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='ai_collect_templates' AND column_name='template_object_key') THEN
        EXECUTE 'UPDATE public.ai_collect_templates SET template = template_object_key WHERE template = ''''';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='public' AND table_name='ai_collect_templates' AND column_name='favicon_object_key') THEN
        EXECUTE 'UPDATE public.ai_collect_templates SET icon = favicon_object_key WHERE icon = ''''';
    END IF;
END $$;
ALTER TABLE public.ai_collect_templates DROP COLUMN IF EXISTS favicon_data;
ALTER TABLE public.ai_collect_templates DROP COLUMN IF EXISTS favicon_url;
ALTER TABLE public.ai_collect_templates DROP COLUMN IF EXISTS favicon_mime;
ALTER TABLE public.ai_collect_templates DROP COLUMN IF EXISTS favicon_sha256;
ALTER TABLE public.ai_collect_templates DROP COLUMN IF EXISTS favicon_size;
ALTER TABLE public.ai_collect_templates DROP COLUMN IF EXISTS template_object_key;
ALTER TABLE public.ai_collect_templates DROP COLUMN IF EXISTS template_sha256;
ALTER TABLE public.ai_collect_templates DROP COLUMN IF EXISTS template_size;
ALTER TABLE public.ai_collect_templates DROP COLUMN IF EXISTS yaml_content;
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
_ICON_URL_PREFIX = "/api/ai/workspace/template-icons"


class AICollectStore:
    def __init__(self) -> None:
        self._pg = get_pg_client()
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self._pg.init_schema([_DDL])
        await self._import_local_templates()
        await self._sync_minio_artifacts()
        self._initialized = True

    @staticmethod
    def _resolved_template_base_url(raw: dict[str, Any]) -> str:
        base_url = str(raw.get("base_url") or "")
        for param in raw.get("params") or []:
            if not isinstance(param, dict):
                continue
            name = str(param.get("name") or "")
            default = str(param.get("default") or "")
            if name and default:
                base_url = base_url.replace(f"{{{name}}}", default)
        return base_url

    async def _sync_minio_artifacts(self, template_id: Any | None = None) -> None:
        if not settings.minio_endpoint or not settings.minio_bucket:
            raise RuntimeError("MinIO is required for AI Collect template artifacts")
        templates_by_name: dict[str, dict[str, Any]] = {}
        template_content_by_name: dict[str, bytes] = {}
        template_dir = Path(settings.template_dir)
        for path in [*template_dir.glob("*.yaml"), *template_dir.glob("*.yml")]:
            try:
                content = path.read_bytes()
                raw = yaml.safe_load(content.decode("utf-8"))
            except Exception:
                continue
            if isinstance(raw, dict):
                template_name = str(raw.get("name") or path.stem)
                templates_by_name[template_name] = raw
                template_content_by_name[template_name] = content

        query = "SELECT * FROM public.ai_collect_templates"
        params: dict[str, Any] = {}
        if template_id is not None:
            query += " WHERE id = CAST(:id AS uuid)"
            params["id"] = template_id
        rows = await self._pg.fetch_all(query, params)
        minio = get_business_metadata_minio_client()
        favicon_cache: dict[str, tuple[bytes, str]] = {}
        async with curl_requests.AsyncSession(
            impersonate="chrome120",
            timeout=30,
            verify=settings.http_verify_ssl,
            allow_redirects=True,
        ) as client:
            for row in rows:
                safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(row["name"])).strip("._") or "template"
                safe_version = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(row["version"])).strip("._") or "v1"
                object_prefix = f"collection/templates/{safe_name}/{safe_version}"
                template_key = str(row.get("template") or f"{object_prefix}/template.yaml")
                template_bytes = template_content_by_name.get(str(row["name"]))
                if template_bytes is not None:
                    template_key = f"{object_prefix}/template.yaml"
                    await minio.upload_bytes_to_key(template_bytes, template_key, "application/yaml")
                raw = templates_by_name.get(str(row["name"]), {})
                resolved_base_url = self._resolved_template_base_url(raw)
                parsed_base_url = urlparse(resolved_base_url)
                favicon_bytes: bytes | None = None
                favicon_mime = "image/x-icon"
                favicon_key = str(row.get("icon") or "")
                if favicon_key:
                    favicon_bytes = await minio.get_object_bytes(favicon_key)
                cache_key = parsed_base_url.hostname or str(row.get("domain") or "")
                if favicon_bytes is None and cache_key in favicon_cache:
                    favicon_bytes, favicon_mime = favicon_cache[cache_key]
                if favicon_bytes is None:
                    candidates: list[str] = []
                    if parsed_base_url.scheme and parsed_base_url.netloc:
                        candidates.append(f"{parsed_base_url.scheme}://{parsed_base_url.netloc}/favicon.ico")
                        try:
                            page_response = await client.get(resolved_base_url)
                            page_response.raise_for_status()
                            tree = lxml_html.fromstring(page_response.text, base_url=resolved_base_url)
                            icon_href = next(iter(tree.xpath("//link[contains(translate(@rel, 'ICON', 'icon'), 'icon')]/@href")), "")
                            if icon_href:
                                candidates.insert(0, urljoin(resolved_base_url, icon_href))
                        except Exception:
                            pass
                    if cache_key:
                        candidates.append(f"https://icon.horse/icon/{cache_key}")
                    for candidate in dict.fromkeys(candidates):
                        try:
                            response = await client.get(candidate)
                            response.raise_for_status()
                            candidate_bytes = response.content
                            if not candidate_bytes or len(candidate_bytes) > 1024 * 1024:
                                continue
                            favicon_bytes = candidate_bytes
                            favicon_mime = response.headers.get("content-type", "image/x-icon").split(";", 1)[0].strip()
                            if cache_key:
                                favicon_cache[cache_key] = (favicon_bytes, favicon_mime)
                            break
                        except Exception:
                            continue
                if favicon_bytes and len(favicon_bytes) <= 1024 * 1024:
                    if not favicon_mime.startswith("image/"):
                        favicon_mime = "image/x-icon"
                    favicon_key = f"{object_prefix}/favicon.ico"
                    await minio.upload_bytes_to_key(favicon_bytes, favicon_key, favicon_mime)
                await self._pg.execute(
                    """
                    UPDATE public.ai_collect_templates
                    SET template = :template,
                        icon = :icon
                    WHERE id = :id
                    """,
                    {
                        "id": row["id"],
                        "template": template_key,
                        "icon": favicon_key,
                    },
                )

    async def get_template_icon(self, filename: str) -> dict[str, Any] | None:
        return await self._pg.fetch_one(
            """
            SELECT icon
            FROM public.ai_collect_templates
            WHERE name = :name AND icon <> ''
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            {"name": filename.removesuffix(".ico")},
        )

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
        row = await self._pg.fetch_one(
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
        if row is not None:
            await self._sync_minio_artifacts(template_id)
            row = await self._pg.fetch_one(
                "SELECT * FROM public.ai_collect_templates WHERE id = CAST(:id AS uuid)",
                {"id": template_id},
            )
        return row

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
        await self._sync_minio_artifacts(row["id"])
        synced = await self._pg.fetch_one(
            "SELECT * FROM public.ai_collect_templates WHERE id = :id",
            {"id": row["id"]},
        )
        return synced or row

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
