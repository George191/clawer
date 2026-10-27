from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urljoin, urlparse

import yaml
from curl_cffi import requests as curl_requests
from lxml import html as lxml_html

from app.config.settings import settings
from app.logger import get_logger
from app.storage.minio_client import get_business_metadata_minio_client
from app.web.services.automation_task_store import AutomationTaskStore

logger = get_logger(__name__)

class AICollectStore(AutomationTaskStore):
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
        """Connect runtime storage; schema changes use explicit migrations."""
        async with self._initialize_lock:
            await self._pg.connect()

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
        from app.adapters.runtime import validate_extension_source

        validate_extension_source(adapter_code)
        object_key = f"{self._artifact_prefix(name, version)}/adapter.py"
        await get_business_metadata_minio_client().upload_bytes_to_key(
            adapter_code.encode("utf-8"),
            object_key,
            "text/x-python",
        )
        return object_key

    async def upload_batch_input(
        self,
        name: str,
        version: str,
        filename: str,
        stream: BinaryIO,
        size: int,
        content_type: str,
    ) -> str:
        safe_filename = re.sub(
            r"[^A-Za-z0-9_.-]+", "_", Path(filename).name
        ).strip("._") or "batch-input.txt"
        object_key = (
            f"{self._artifact_prefix(name, version)}/batch-inputs/"
            f"{uuid.uuid4().hex}-{safe_filename}"
        )
        return await get_business_metadata_minio_client().upload_stream_to_key(
            stream,
            size,
            object_key,
            content_type,
        )

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
                   COALESCE((SELECT count(*) FROM public.automation_tasks task
                             WHERE task.template_name = t.name
                               AND task.deleted_at IS NULL), 0)::int AS task_count
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

    async def list_template_definitions(
        self,
        *,
        include_adapter_code: bool = False,
    ) -> list[dict[str, Any]]:
        await self.initialize()
        rows = await self._pg.fetch_all(
            """
            SELECT t.*,
                   COALESCE((SELECT count(*) FROM public.automation_tasks task
                             WHERE task.template_name = t.name
                               AND task.deleted_at IS NULL), 0)::int AS task_count
            FROM public.ai_collect_templates t
            ORDER BY t.updated_at DESC
            """
        )
        minio = get_business_metadata_minio_client()
        definitions: list[dict[str, Any]] = []
        for row in rows:
            template_key = str(row.get("template") or "")
            template_bytes = await minio.get_object_bytes(template_key)
            if template_bytes is None:
                raise RuntimeError(f"MinIO template object is unavailable: {template_key}")
            try:
                row["yaml_content"] = template_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RuntimeError(
                    f"Released template is not valid UTF-8: {row['name']}@{row['version']}"
                ) from exc
            row["adapter_code"] = ""
            adapter_key = str(row.get("adapter") or "")
            if include_adapter_code and adapter_key:
                adapter_bytes = await minio.get_object_bytes(adapter_key)
                if adapter_bytes is None:
                    raise RuntimeError(
                        f"MinIO adapter object is unavailable: {adapter_key}"
                    )
                try:
                    row["adapter_code"] = adapter_bytes.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise RuntimeError(
                        f"Released adapter is not valid UTF-8: "
                        f"{row['name']}@{row['version']}"
                    ) from exc
            definitions.append(row)
        return definitions

    async def get_template_definition(
        self,
        name: str,
        version: str | None = None,
    ) -> dict[str, Any] | None:
        await self.initialize()
        version_clause = "AND t.version = :version" if version is not None else ""
        params: dict[str, Any] = {"name": name}
        if version is not None:
            params["version"] = version
        row = await self._pg.fetch_one(
            f"""
            SELECT t.*,
                   COALESCE((SELECT count(*) FROM public.automation_tasks task
                             WHERE task.template_name = t.name
                               AND task.deleted_at IS NULL), 0)::int AS task_count
            FROM public.ai_collect_templates t
            WHERE t.name = :name {version_clause}
            ORDER BY (t.status = 'active') DESC, t.updated_at DESC
            LIMIT 1
            """,
            params,
        )
        if row is None:
            return None
        template_key = str(row.get("template") or "")
        template_bytes = await get_business_metadata_minio_client().get_object_bytes(
            template_key
        )
        if template_bytes is None:
            raise RuntimeError(f"MinIO template object is unavailable: {template_key}")
        try:
            row["yaml_content"] = template_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError(
                f"Released template is not valid UTF-8: {row['name']}@{row['version']}"
            ) from exc
        return row

    async def delete_template_definition(self, name: str, version: str) -> bool:
        await self.initialize()
        row = await self._pg.fetch_one(
            """
            DELETE FROM public.ai_collect_templates target
            WHERE target.name = :name
              AND target.version = :version
              AND NOT EXISTS (
                  SELECT 1 FROM public.automation_tasks task
                  WHERE task.template_name = target.name
                    AND task.deleted_at IS NULL
              )
            RETURNING target.template, target.adapter, target.icon
            """,
            {"name": name, "version": version},
        )
        if row is None:
            return False
        minio = get_business_metadata_minio_client()
        for object_key in dict.fromkeys(
            str(row.get(column) or "") for column in ("template", "adapter", "icon")
        ):
            if not object_key:
                continue
            try:
                await minio.remove_object(object_key)
            except Exception:
                logger.exception("Failed to clean deleted template object: %s", object_key)
        return True

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
        return await super().list_tasks(product_domain="ai-collect")

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        task = await super().get_task(task_id)
        if task is None or task.get("product_domain") != "ai-collect":
            return None
        return task


ai_collect_store = AICollectStore()
