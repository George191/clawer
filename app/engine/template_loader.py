"""模板加载器 — 从 YAML 文件加载 SiteTemplate 配置并进行参数代入。

支持：
- templates/ 目录下的 YAML 模板加载
- 模板参数占位符替换（如 {keyword} -> LED）
- 多模板批量加载
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.adapters import BaseSiteAdapter, load_adapter_class_from_source
from app.config.settings import settings
from app.logger import get_logger
from app.models.template import SiteTemplate
from app.storage.minio_client import get_business_metadata_minio_client
from app.storage.postgres_client import get_pg_client

logger = get_logger(__name__)


@dataclass(frozen=True)
class ReleasedTemplate:
    template: SiteTemplate
    yaml_content: str
    template_key: str
    adapter_key: str
    adapter_class: type[BaseSiteAdapter] | None


class TemplateLoader:
    def __init__(self, template_dir: str | None = None) -> None:
        self._template_dir = Path(template_dir or settings.template_dir)

    def load(
        self,
        name: str,
        param_values: dict[str, str] | None = None,
        *,
        validate_params: bool = True,
    ) -> SiteTemplate:
        file_path = self._resolve_template_file(name)
        raw = self._read_yaml(file_path)
        return self._build_template(raw, param_values, validate_params)

    def load_content(
        self,
        content: str,
        param_values: dict[str, str] | None = None,
        *,
        validate_params: bool = True,
        source: str = "<memory>",
    ) -> SiteTemplate:
        raw = self._read_yaml_content(content, source)
        return self._build_template(raw, param_values, validate_params)

    async def load_released(
        self,
        name: str,
        version: str | None = None,
        param_values: dict[str, str] | None = None,
        *,
        validate_params: bool = True,
        load_adapter: bool = True,
    ) -> ReleasedTemplate:
        version_clause = "AND version = :version" if version is not None else ""
        params: dict[str, Any] = {"name": name}
        if version is not None:
            params["version"] = version
        row = await get_pg_client().fetch_one(
            f"""
            SELECT name, version, template, adapter
            FROM public.ai_collect_templates
            WHERE name = :name {version_clause}
            ORDER BY (status = 'active') DESC, updated_at DESC
            LIMIT 1
            """,
            params,
        )
        if row is None:
            requested = f"{name}@{version}" if version is not None else name
            raise FileNotFoundError(f"Released template not found: {requested}")

        minio = get_business_metadata_minio_client()
        template_key = str(row.get("template") or "")
        template_bytes = await minio.get_object_bytes(template_key)
        if template_bytes is None:
            raise RuntimeError(f"MinIO template object is unavailable: {template_key}")
        try:
            yaml_content = template_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError(
                f"Released template is not valid UTF-8: {row['name']}@{row['version']}"
            ) from exc

        template = self.load_content(
            yaml_content,
            param_values=param_values,
            validate_params=validate_params,
            source=template_key,
        )
        if template.name != name:
            raise RuntimeError(
                f"Released template name mismatch: expected {name}, got {template.name}"
            )

        adapter_key = str(row.get("adapter") or "")
        adapter_class: type[BaseSiteAdapter] | None = None
        if template.adapter and load_adapter:
            if not adapter_key:
                raise RuntimeError(
                    f"Released adapter is missing: {row['name']}@{row['version']}"
                )
            adapter_bytes = await minio.get_object_bytes(adapter_key)
            if adapter_bytes is None:
                raise RuntimeError(f"MinIO adapter object is unavailable: {adapter_key}")
            try:
                adapter_code = adapter_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RuntimeError(
                    f"Released adapter is not valid UTF-8: {row['name']}@{row['version']}"
                ) from exc
            adapter_class = load_adapter_class_from_source(
                template.adapter,
                adapter_code,
                adapter_key,
            )

        return ReleasedTemplate(
            template=template,
            yaml_content=yaml_content,
            template_key=template_key,
            adapter_key=adapter_key,
            adapter_class=adapter_class,
        )

    def _build_template(
        self,
        raw: dict[str, Any],
        param_values: dict[str, str] | None,
        validate_params: bool,
    ) -> SiteTemplate:
        raw = self._normalize_download_config(raw)
        template = SiteTemplate(**raw)
        if template.params and (param_values is not None or validate_params):
            template.apply_params(param_values)
        return template

    def load_all(self) -> list[SiteTemplate]:
        templates: list[SiteTemplate] = []
        if not self._template_dir.exists():
            logger.warning("Template directory does not exist: %s", self._template_dir)
            return templates

        for file_path in sorted(self._template_dir.glob("*.yaml")):
            try:
                raw = self._read_yaml(file_path)
                template = SiteTemplate(**raw)
                templates.append(template)
                logger.info("Loaded template: %s from %s", template.name, file_path)
            except Exception as e:
                logger.error("Failed to load template %s: %s", file_path, e)

        for file_path in sorted(self._template_dir.glob("*.yml")):
            try:
                raw = self._read_yaml(file_path)
                template = SiteTemplate(**raw)
                templates.append(template)
                logger.info("Loaded template: %s from %s", template.name, file_path)
            except Exception as e:
                logger.error("Failed to load template %s: %s", file_path, e)

        return templates

    def _resolve_template_file(self, name: str) -> Path:
        for ext in (".yaml", ".yml"):
            path = self._template_dir / f"{name}{ext}"
            if path.exists():
                return path
        raise FileNotFoundError(
            f"Template '{name}' not found in {self._template_dir}"
        )

    @staticmethod
    def _read_yaml(file_path: Path) -> dict[str, Any]:
        content = file_path.read_text(encoding="utf-8")
        return TemplateLoader._read_yaml_content(content, str(file_path))

    @staticmethod
    def _read_yaml_content(content: str, source: str) -> dict[str, Any]:
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            raise ValueError(f"Template must contain a YAML mapping: {source}")
        return data

    @staticmethod
    def _normalize_download_config(raw: dict[str, Any]) -> dict[str, Any]:
        """向后兼容: 将单个 download 对象自动包装为列表。"""
        download = raw.get("download")
        if download is None:
            return raw
        if isinstance(download, list):
            return raw
        if isinstance(download, dict):
            raw["download"] = [download]
        return raw
