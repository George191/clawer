"""模板加载器 — 从 YAML 文件加载 SiteTemplate 配置并进行参数代入。

支持：
- templates/ 目录下的 YAML 模板加载
- 模板参数占位符替换（如 {keyword} -> LED）
- 多模板批量加载
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.config.settings import settings
from app.models.template import SiteTemplate

logger = logging.getLogger(__name__)


class TemplateLoader:
    def __init__(self, template_dir: str | None = None) -> None:
        self._template_dir = Path(template_dir or settings.template_dir)

    def load(self, name: str, param_values: dict[str, str] | None = None) -> SiteTemplate:
        file_path = self._resolve_template_file(name)
        raw = self._read_yaml(file_path)
        template = SiteTemplate(**raw)
        if template.params or param_values:
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
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            raise ValueError(f"Template file must contain a YAML mapping: {file_path}")
        return data
