"""模板管理 API — YAML 采集模板的 CRUD 操作。

Endpoints:
    GET    /api/templates         — 模板列表
    GET    /api/templates/{name}  — 模板详情（含 YAML 内容）
    POST   /api/templates         — 创建新模板
    PUT    /api/templates/{name}  — 更新模板 YAML
    DELETE /api/templates/{name}  — 删除模板
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()

TEMPLATE_DIR = Path(settings.template_dir)


class TemplateCreateRequest(BaseModel):
    """创建模板请求体."""

    name: str = Field(..., description="唯一模板名称, 如 google_patent")
    display_name: str = Field(default="", description="显示名称")
    base_url: str = Field(..., description="网站基础 URL")
    data_type: str = Field(default="other", description="数据类型: patent / contract / other")
    description: str = Field(default="", description="模板描述")
    yaml_content: str = Field(default="", description="完整 YAML 内容（可选，覆盖字段参数）")


class TemplateUpdateRequest(BaseModel):
    """更新模板请求体."""

    yaml_content: str = Field(..., description="完整的 YAML 模板内容")


@router.get("/templates")
async def list_templates() -> list[dict[str, Any]]:
    """获取所有模板列表。

    返回模板摘要信息：name、type、description、status 和 fields 列表。
    """
    templates: list[dict[str, Any]] = []
    if not TEMPLATE_DIR.exists():
        return templates

    for ext in ("*.yaml", "*.yml"):
        for file_path in sorted(TEMPLATE_DIR.glob(ext)):
            try:
                raw = _read_yaml_safe(file_path)
                fields = _extract_fields(raw)
                templates.append({
                    "name": raw.get("name", file_path.stem),
                    "type": raw.get("data_type", "unknown"),
                    "description": raw.get("description", ""),
                    "status": "active",
                    "fields": fields,
                })
            except Exception as e:
                logger.warning("Failed to read template %s: %s", file_path, e)

    return templates


@router.get("/templates/{name}")
async def get_template(name: str) -> dict[str, Any]:
    """获取模板详情。

    返回模板元信息及完整 YAML 文本内容。
    """
    file_path = _resolve_template_file(name)
    if file_path is None:
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")

    try:
        raw = _read_yaml_safe(file_path)
        yaml_content = file_path.read_text(encoding="utf-8")
        fields = _extract_fields(raw)
        return {
            "name": raw.get("name", name),
            "display_name": raw.get("display_name", ""),
            "base_url": raw.get("base_url", ""),
            "data_type": raw.get("data_type", "unknown"),
            "description": raw.get("description", ""),
            "fields": fields,
            "yaml_content": yaml_content,
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read template '{name}': {e}",
        )


@router.post("/templates")
async def create_template(body: TemplateCreateRequest) -> dict[str, Any]:
    """创建新的 YAML 模板文件。

    如果提供了 yaml_content 则直接写入，否则根据请求字段生成 YAML。
    """
    file_path = TEMPLATE_DIR / f"{body.name}.yaml"
    if file_path.exists():
        raise HTTPException(
            status_code=400,
            detail=f"Template '{body.name}' already exists",
        )

    if body.yaml_content:
        content = body.yaml_content
    else:
        content = _build_yaml(body)

    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    logger.info("Created template: %s", file_path)

    return {"name": body.name, "message": "Template created"}


@router.put("/templates/{name}")
async def update_template(name: str, body: TemplateUpdateRequest) -> dict[str, Any]:
    """更新已有模板的 YAML 内容。"""
    file_path = _resolve_template_file(name)
    if file_path is None:
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")

    file_path.write_text(body.yaml_content, encoding="utf-8")
    logger.info("Updated template: %s", file_path)

    return {"name": name, "message": "Template updated"}


@router.delete("/templates/{name}")
async def delete_template(name: str) -> dict[str, Any]:
    """删除指定模板文件。"""
    file_path = _resolve_template_file(name)
    if file_path is None:
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")

    file_path.unlink()
    logger.info("Deleted template: %s", file_path)

    return {"name": name, "message": "Template deleted"}


# ── Helpers ─────────────────────────────────────────────────────────────────


def _resolve_template_file(name: str) -> Path | None:
    """解析模板文件路径，按 .yaml / .yml 顺序查找。"""
    for ext in (".yaml", ".yml"):
        path = TEMPLATE_DIR / f"{name}{ext}"
        if path.exists():
            return path
    return None


def _read_yaml_safe(file_path: Path) -> dict[str, Any]:
    """安全读取 YAML 文件，返回字典。"""
    content = file_path.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise ValueError("Template file must contain a YAML mapping")
    return data


def _extract_fields(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """从原始 YAML 数据中提取字段列表摘要。"""
    fields: list[dict[str, Any]] = []
    for section in ("list_fields", "detail_fields"):
        for f in raw.get(section, []):
            if isinstance(f, dict):
                fields.append({
                    "name": f.get("name", ""),
                    "type": f.get("field_type", f.get("type", "text")),
                    "required": f.get("required", True),
                    "section": section.replace("_fields", ""),
                })
    return fields


def _build_yaml(body: TemplateCreateRequest) -> str:
    """根据请求字段生成基础 YAML 模板内容。"""
    return (
        f"name: {body.name}\n"
        f"display_name: \"{body.display_name or body.name}\"\n"
        f"base_url: \"{body.base_url}\"\n"
        f"data_type: {body.data_type}\n"
        f"description: >\n  {body.description or body.name}\n"
        f"\n"
        f"response_type: html\n"
        f"list_page: \"/\"\n"
        f"list_request:\n"
        f"  method: GET\n"
        f"  headers:\n"
        f"    Accept: \"text/html\"\n"
    )
