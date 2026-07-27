from __future__ import annotations

import asyncio
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
    data_type text NOT NULL DEFAULT 'other',
    template text NOT NULL DEFAULT '',
    icon text NOT NULL DEFAULT '',
    status text NOT NULL DEFAULT 'draft' CHECK (status IN ('active', 'draft', 'deprecated')),
    adapter text NOT NULL DEFAULT '',
    description text NOT NULL DEFAULT '',
    owner text NOT NULL DEFAULT 'AI Collect',
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (name, version)
);
ALTER TABLE public.ai_collect_templates ADD COLUMN IF NOT EXISTS template text NOT NULL DEFAULT '';
ALTER TABLE public.ai_collect_templates ADD COLUMN IF NOT EXISTS icon text NOT NULL DEFAULT '';
ALTER TABLE public.ai_collect_templates ADD COLUMN IF NOT EXISTS data_type text NOT NULL DEFAULT 'other';
CREATE TABLE IF NOT EXISTS public.ai_collect_tasks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    template_name text NOT NULL,
    template_version text NOT NULL DEFAULT 'v1.0',
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'completed', 'failed', 'paused')),
    progress integer NOT NULL DEFAULT 0,
    records bigint NOT NULL DEFAULT 0,
    throughput integer NOT NULL DEFAULT 0,
    inserted_records bigint NOT NULL DEFAULT 0,
    updated_records bigint NOT NULL DEFAULT 0,
    deleted_records bigint NOT NULL DEFAULT 0,
    downloaded_records bigint NOT NULL DEFAULT 0,
    synced_records bigint NOT NULL DEFAULT 0,
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
ALTER TABLE public.ai_collect_tasks ADD COLUMN IF NOT EXISTS inserted_records bigint NOT NULL DEFAULT 0;
ALTER TABLE public.ai_collect_tasks ADD COLUMN IF NOT EXISTS updated_records bigint NOT NULL DEFAULT 0;
ALTER TABLE public.ai_collect_tasks ADD COLUMN IF NOT EXISTS deleted_records bigint NOT NULL DEFAULT 0;
ALTER TABLE public.ai_collect_tasks ADD COLUMN IF NOT EXISTS downloaded_records bigint NOT NULL DEFAULT 0;
ALTER TABLE public.ai_collect_tasks ADD COLUMN IF NOT EXISTS synced_records bigint NOT NULL DEFAULT 0;
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
        self._initialize_lock = asyncio.Lock()

    @staticmethod
    def _is_image_content(content: bytes) -> bool:
        stripped = content.lstrip()
        return (
            content.startswith(b"\x00\x00\x01\x00")
            or content.startswith(b"\x89PNG\r\n\x1a\n")
            or content.startswith(b"\xff\xd8\xff")
            or content.startswith((b"GIF87a", b"GIF89a"))
            or (content.startswith(b"RIFF") and content[8:12] == b"WEBP")
            or (len(content) >= 12 and content[4:12] in {b"ftypavif", b"ftypavis"})
            or stripped.startswith(b"<svg")
            or (stripped.startswith(b"<?xml") and b"<svg" in stripped[:512])
        )

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._initialize_lock:
            if self._initialized:
                return
            await self._pg.init_schema([_DDL])
            await self._migrate_artifact_columns()
            await self._import_local_templates()
            self._initialized = True

    async def _migrate_artifact_columns(self) -> None:
        rows = await self._pg.fetch_all(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'ai_collect_templates'
            """
        )
        columns = {str(row["column_name"]) for row in rows}
        if "template_object_key" in columns:
            await self._pg.execute(
                """
                UPDATE public.ai_collect_templates
                SET template = template_object_key
                WHERE template = '' AND template_object_key <> ''
                """
            )
        if "favicon_object_key" in columns:
            await self._pg.execute(
                """
                UPDATE public.ai_collect_templates
                SET icon = favicon_object_key
                WHERE icon = '' AND favicon_object_key <> ''
                """
            )
        for column in (
            "favicon_data",
            "favicon_url",
            "favicon_mime",
            "favicon_sha256",
            "favicon_size",
            "favicon_object_key",
            "template_object_key",
            "template_sha256",
            "template_size",
            "yaml_content",
            "output_tag",
        ):
            await self._pg.execute(
                f"ALTER TABLE public.ai_collect_templates DROP COLUMN IF EXISTS {column}"
            )

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
        if not settings.minio_endpoint or not settings.business_metadata_minio_bucket:
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
                    if favicon_bytes and not self._is_image_content(favicon_bytes):
                        favicon_bytes = None
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
                            if (
                                not candidate_bytes
                                or len(candidate_bytes) > 1024 * 1024
                                or not self._is_image_content(candidate_bytes)
                            ):
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
                adapter_path = Path("app/adapters") / f"{name}.py"
                await self._pg.execute(
                    """
                    INSERT INTO public.ai_collect_templates
                        (name, version, title, domain, data_type, status, adapter, description)
                    VALUES
                        (:name, :version, :title, :domain, :data_type, 'active', :adapter, :description)
                    ON CONFLICT (name, version) DO UPDATE SET
                        data_type = EXCLUDED.data_type
                    WHERE public.ai_collect_templates.data_type = 'other'
                      AND EXCLUDED.data_type <> 'other'
                    """,
                    {
                        "name": name,
                        "version": version,
                        "title": title,
                        "domain": domain,
                        "data_type": str(raw.get("data_type") or "other"),
                        "adapter": adapter_path.as_posix() if adapter_path.exists() else "",
                        "description": str(raw.get("description") or ""),
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

    @staticmethod
    def _artifact_prefix(name: str, version: str) -> str:
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("._") or "template"
        safe_version = re.sub(r"[^A-Za-z0-9_.-]+", "_", version).strip("._") or "v1"
        return f"collection/templates/{safe_name}/{safe_version}"

    @staticmethod
    def _template_data_type(template_yaml: str) -> str:
        try:
            raw = yaml.safe_load(template_yaml)
        except yaml.YAMLError:
            return "other"
        if not isinstance(raw, dict):
            return "other"
        return str(raw.get("data_type") or "other").strip().lower() or "other"

    async def _upload_template_yaml(self, name: str, version: str, yaml_content: str) -> str:
        object_key = f"{self._artifact_prefix(name, version)}/template.yaml"
        await get_business_metadata_minio_client().upload_bytes_to_key(
            yaml_content.encode("utf-8"),
            object_key,
            "application/yaml",
        )
        return object_key

    async def _upload_adapter_code(self, name: str, version: str, adapter_code: str) -> str:
        object_key = f"{self._artifact_prefix(name, version)}/adapter.py"
        await get_business_metadata_minio_client().upload_bytes_to_key(
            adapter_code.encode("utf-8"),
            object_key,
            "text/x-python",
        )
        return object_key

    async def _upload_favicon(self, name: str, version: str, source_url: str) -> str:
        if not source_url.startswith(("http://", "https://")):
            return ""
        try:
            async with curl_requests.AsyncSession(
                impersonate="chrome120",
                timeout=30,
                verify=settings.http_verify_ssl,
                allow_redirects=True,
            ) as client:
                response = await client.get(source_url)
                response.raise_for_status()
                content = response.content
                if not content or len(content) > 1024 * 1024 or not self._is_image_content(content):
                    return ""
                object_key = f"{self._artifact_prefix(name, version)}/favicon.ico"
                content_type = response.headers.get("content-type", "image/x-icon").split(";", 1)[0].strip()
                await get_business_metadata_minio_client().upload_bytes_to_key(
                    content,
                    object_key,
                    content_type if content_type.startswith("image/") else "image/x-icon",
                )
                return object_key
        except Exception:
            return ""

    async def list_templates(self) -> list[dict[str, Any]]:
        await self.initialize()
        rows = await self._pg.fetch_all(
            """
            SELECT t.*,
                   COALESCE((SELECT count(*) FROM public.ai_collect_tasks task
                             WHERE task.template_name = t.name), 0)::int AS task_count
            FROM public.ai_collect_templates t
            ORDER BY t.updated_at DESC
            """
        )
        minio = get_business_metadata_minio_client()
        for row in rows:
            template_ref = str(row.get("template") or "")
            icon_ref = str(row.get("icon") or "")
            adapter_ref = str(row.get("adapter") or "")
            row["template_path"] = template_ref
            row["template"] = minio.build_object_url(template_ref)
            row["icon"] = minio.build_object_url(icon_ref)
            row["favicon_url"] = row["icon"]
            if adapter_ref and not adapter_ref.startswith("app/adapters/"):
                row["adapter"] = minio.build_object_url(adapter_ref)
        return rows

    async def update_template(self, template_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        await self.initialize()
        current = await self._pg.fetch_one(
            "SELECT name, version FROM public.ai_collect_templates WHERE id = CAST(:id AS uuid)",
            {"id": template_id},
        )
        if current is None:
            return None
        template_key = await self._upload_template_yaml(
            str(current["name"]),
            str(current["version"]),
            str(payload["yaml_content"]),
        )
        data_type = self._template_data_type(str(payload["yaml_content"]))
        adapter_code = str(payload.get("adapter_code") or "")
        adapter_key = (
            await self._upload_adapter_code(str(current["name"]), str(current["version"]), adapter_code)
            if adapter_code else str(payload.get("adapter") or "")
        )
        row = await self._pg.fetch_one(
            """
            UPDATE public.ai_collect_templates SET
                template = :template,
                adapter = :adapter,
                data_type = :data_type,
                description = :description,
                updated_at = now()
            WHERE id = CAST(:id AS uuid)
            RETURNING *
            """,
            {
                "id": template_id,
                "template": template_key,
                "data_type": data_type,
                **payload,
                "adapter": adapter_key,
            },
        )
        if row is not None:
            row["yaml_content"] = payload["yaml_content"]
            row["adapter_code"] = adapter_code
            row["favicon_url"] = get_business_metadata_minio_client().build_object_url(
                str(row.get("icon") or "")
            )
        return row

    async def release_template(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self.initialize()
        template_key = await self._upload_template_yaml(
            str(payload["name"]),
            str(payload["version"]),
            str(payload["yaml_content"]),
        )
        data_type = self._template_data_type(str(payload["yaml_content"]))
        adapter_code = str(payload.get("adapter_code") or "")
        adapter_key = (
            await self._upload_adapter_code(str(payload["name"]), str(payload["version"]), adapter_code)
            if adapter_code else str(payload.get("adapter") or "")
        )
        existing = await self._pg.fetch_one(
            "SELECT icon FROM public.ai_collect_templates WHERE name = :name AND version = :version",
            {"name": payload["name"], "version": payload["version"]},
        )
        icon_key = await self._upload_favicon(
            str(payload["name"]),
            str(payload["version"]),
            str(payload.get("favicon_url") or ""),
        ) or str(existing.get("icon") if existing else "")
        row = await self._pg.fetch_one(
            """
            INSERT INTO public.ai_collect_templates
                (name, version, title, domain, data_type, template, icon, status, adapter, description, metadata)
            VALUES
                (:name, :version, :title, :domain, :data_type, :template, :icon, :status, :adapter,
                 :description, CAST(:metadata AS jsonb))
            ON CONFLICT (name, version) DO UPDATE SET
                title = EXCLUDED.title,
                domain = EXCLUDED.domain,
                data_type = EXCLUDED.data_type,
                template = EXCLUDED.template,
                icon = EXCLUDED.icon,
                status = EXCLUDED.status,
                adapter = EXCLUDED.adapter,
                description = EXCLUDED.description,
                metadata = EXCLUDED.metadata,
                updated_at = now()
            RETURNING *
            """,
            {
                **payload,
                "template": template_key,
                "data_type": data_type,
                "adapter": adapter_key,
                "icon": icon_key,
                "metadata": json.dumps(payload.get("metadata", {}), ensure_ascii=False),
            },
        )
        if row is None:
            raise RuntimeError("Template release did not return a row")
        row["yaml_content"] = payload["yaml_content"]
        row["adapter_code"] = adapter_code
        row["favicon_url"] = get_business_metadata_minio_client().build_object_url(
            str(row.get("icon") or "")
        )
        return row

    async def list_tasks(self) -> list[dict[str, Any]]:
        await self.initialize()
        return await self._pg.fetch_all(
            """
            SELECT task.*, '[]'::jsonb AS logs
            FROM public.ai_collect_tasks task
            ORDER BY task.updated_at DESC
            """
        )

    async def append_task_log(self, task_id: str, level: str, message: str) -> None:
        await self.initialize()
        await self._pg.execute(
            """
            INSERT INTO public.ai_collect_task_logs (task_id, level, message)
            VALUES (CAST(:task_id AS uuid), :level, :message)
            """,
            {"task_id": task_id, "level": level, "message": message},
        )

    async def increment_task_stats(
        self,
        task_id: str,
        *,
        inserted: int = 0,
        updated: int = 0,
        deleted: int = 0,
        downloaded: int = 0,
        synced: int = 0,
    ) -> None:
        await self.initialize()
        await self._pg.execute(
            """
            UPDATE public.ai_collect_tasks SET
                inserted_records = inserted_records + :inserted,
                updated_records = updated_records + :updated,
                deleted_records = deleted_records + :deleted,
                downloaded_records = downloaded_records + :downloaded,
                synced_records = synced_records + :synced,
                updated_at = now()
            WHERE id = CAST(:task_id AS uuid)
            """,
            {
                "task_id": task_id,
                "inserted": inserted,
                "updated": updated,
                "deleted": deleted,
                "downloaded": downloaded,
                "synced": synced,
            },
        )

    async def create_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        await self.initialize()
        task = await self._pg.fetch_one(
            """
            INSERT INTO public.ai_collect_tasks
                (name, template_name, template_version, status, schedule, parameters, policies, owner, started_at)
            VALUES
                (:name, :template_name, :template_version, 'queued', CAST(:schedule AS jsonb),
                 CAST(:parameters AS jsonb), CAST(:policies AS jsonb), :owner, NULL)
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
        task["logs"] = []
        return task

    async def update_task(self, task_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        await self.initialize()
        task = await self._pg.fetch_one(
            """
            UPDATE public.ai_collect_tasks SET
                status = COALESCE(:status, status),
                progress = COALESCE(:progress, progress),
                records = COALESCE(:records, records),
                throughput = COALESCE(:throughput, throughput),
                inserted_records = COALESCE(:inserted_records, inserted_records),
                updated_records = COALESCE(:updated_records, updated_records),
                deleted_records = COALESCE(:deleted_records, deleted_records),
                downloaded_records = COALESCE(:downloaded_records, downloaded_records),
                synced_records = COALESCE(:synced_records, synced_records),
                control_state = COALESCE(:control_state, control_state),
                download_state = COALESCE(:download_state, download_state),
                sync_state = COALESCE(:sync_state, sync_state),
                started_at = CASE WHEN :start_now THEN now() ELSE started_at END,
                updated_at = now()
            WHERE id = CAST(:id AS uuid)
            RETURNING *
            """,
            {
                "id": task_id,
                "status": None,
                "progress": None,
                "records": None,
                "throughput": None,
                "inserted_records": None,
                "updated_records": None,
                "deleted_records": None,
                "downloaded_records": None,
                "synced_records": None,
                "control_state": None,
                "download_state": None,
                "sync_state": None,
                "start_now": False,
                **changes,
            },
        )
        if task:
            task["logs"] = []
        return task

    async def start_task(self, task_id: str) -> dict[str, Any] | None:
        """Atomically claim a queued workspace task for execution."""
        await self.initialize()
        task = await self._pg.fetch_one(
            """
            UPDATE public.ai_collect_tasks SET
                status = 'running',
                download_state = 'running',
                sync_state = 'running',
                started_at = now(),
                updated_at = now()
            WHERE id = CAST(:id AS uuid) AND status = 'queued'
            RETURNING *
            """,
            {"id": task_id},
        )
        if task:
            task["logs"] = []
        return task

    async def set_active_task_status(
        self,
        task_id: str,
        current_status: str,
        next_status: str,
    ) -> dict[str, Any] | None:
        """Transition an active task only from its expected current state."""
        await self.initialize()
        task = await self._pg.fetch_one(
            """
            UPDATE public.ai_collect_tasks SET
                status = :next_status,
                updated_at = now()
            WHERE id = CAST(:id AS uuid) AND status = :current_status
            RETURNING *
            """,
            {"id": task_id, "current_status": current_status, "next_status": next_status},
        )
        if task:
            task["logs"] = []
        return task

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        await self.initialize()
        return await self._pg.fetch_one(
            """
            SELECT task.*,
                   COALESCE((
                       SELECT jsonb_agg(log_row.payload ORDER BY log_row.created_at)
                       FROM (
                           SELECT created_at,
                                  jsonb_build_object(
                                      'level', level,
                                      'message', message,
                                      'created_at', created_at
                                  ) AS payload
                           FROM public.ai_collect_task_logs
                           WHERE task_id = task.id
                           ORDER BY created_at DESC
                           LIMIT 200
                       ) log_row
                   ), '[]'::jsonb) AS logs
            FROM public.ai_collect_tasks task
            WHERE id = CAST(:id AS uuid)
            """,
            {"id": task_id},
        )

    async def get_task_control(self, task_id: str) -> dict[str, Any] | None:
        await self.initialize()
        return await self._pg.fetch_one(
            """
            SELECT status, control_state, download_state, sync_state
            FROM public.ai_collect_tasks
            WHERE id = CAST(:id AS uuid)
            """,
            {"id": task_id},
        )

    async def delete_task(self, task_id: str) -> bool:
        """Delete an unstarted or terminal task; active tasks must be canceled first."""
        await self.initialize()
        deleted = await self._pg.fetch_one(
            """
            DELETE FROM public.ai_collect_tasks
            WHERE id = CAST(:id AS uuid)
              AND status NOT IN ('running', 'paused')
            RETURNING id
            """,
            {"id": task_id},
        )
        return deleted is not None


ai_collect_store = AICollectStore()
