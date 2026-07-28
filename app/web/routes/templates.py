"""Template CRUD API backed by the released Postgres/MinIO catalog."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.logger import get_logger
from app.web.services.ai_collect_store import ai_collect_store

logger = get_logger(__name__)
router = APIRouter()


class TemplateCreateRequest(BaseModel):
    name: str = Field(..., description="Unique template name")
    display_name: str = Field(default="", description="Display name")
    base_url: str = Field(..., description="Site base URL")
    data_type: str = Field(default="other", description="Business data type")
    description: str = Field(default="", description="Template description")
    yaml_content: str = Field(default="", description="Complete YAML content")


class TemplateUpdateRequest(BaseModel):
    yaml_content: str = Field(..., description="Complete YAML content")


@router.get("/templates")
async def list_templates() -> list[dict[str, Any]]:
    templates: list[dict[str, Any]] = []
    for definition in await ai_collect_store.list_template_definitions():
        raw = _read_yaml_safe(
            str(definition["yaml_content"]),
            str(definition["template"]),
        )
        templates.append({
            "name": raw.get("name", definition["name"]),
            "type": raw.get("data_type", definition.get("data_type", "unknown")),
            "description": raw.get(
                "description", definition.get("description", "")
            ),
            "status": definition.get("status", "active"),
            "fields": _extract_fields(raw),
        })
    return templates


@router.get("/templates/{name}")
async def get_template(name: str) -> dict[str, Any]:
    definition = await ai_collect_store.get_template_definition(name)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")

    try:
        yaml_content = str(definition["yaml_content"])
        raw = _read_yaml_safe(yaml_content, str(definition["template"]))
        return {
            "name": raw.get("name", name),
            "display_name": raw.get("display_name", ""),
            "base_url": raw.get("base_url", ""),
            "data_type": raw.get("data_type", "unknown"),
            "description": raw.get("description", ""),
            "fields": _extract_fields(raw),
            "yaml_content": yaml_content,
        }
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read template '{name}': {exc}",
        ) from exc


@router.post("/templates")
async def create_template(body: TemplateCreateRequest) -> dict[str, Any]:
    if await ai_collect_store.get_template_definition(body.name) is not None:
        raise HTTPException(
            status_code=400,
            detail=f"Template '{body.name}' already exists",
        )

    content = body.yaml_content or _build_yaml(body)
    raw = _read_yaml_safe(content, f"request:{body.name}")
    template_name = str(raw.get("name") or body.name)
    if template_name != body.name:
        raise HTTPException(status_code=400, detail="Template name does not match request")
    if raw.get("adapter"):
        raise HTTPException(
            status_code=400,
            detail="Templates with adapters must be released through AI Collect",
        )

    version = str(raw.get("version") or "v1.0")
    base_url = str(raw.get("base_url") or body.base_url)
    await ai_collect_store.release_template({
        "name": body.name,
        "version": version,
        "title": str(raw.get("display_name") or body.display_name or body.name),
        "domain": urlparse(base_url).hostname or base_url,
        "yaml_content": content,
        "favicon_url": "",
        "status": "active",
        "adapter": "",
        "adapter_code": "",
        "description": str(raw.get("description") or body.description),
        "metadata": {},
    })
    logger.info("Created MinIO template: %s@%s", body.name, version)
    return {"name": body.name, "message": "Template created"}


@router.put("/templates/{name}")
async def update_template(name: str, body: TemplateUpdateRequest) -> dict[str, Any]:
    definition = await ai_collect_store.get_template_definition(name)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")

    raw = _read_yaml_safe(body.yaml_content, f"request:{name}")
    if str(raw.get("name") or name) != name:
        raise HTTPException(status_code=400, detail="Template name does not match request")
    version = str(raw.get("version") or definition["version"])
    if version != str(definition["version"]):
        raise HTTPException(
            status_code=400,
            detail="Template version does not match current version",
        )

    adapter_ref = str(definition.get("adapter") or "") if raw.get("adapter") else ""
    if raw.get("adapter") and not adapter_ref:
        raise HTTPException(
            status_code=400,
            detail="Template declares an adapter but no released adapter exists",
        )
    updated = await ai_collect_store.update_template(
        str(definition["id"]),
        {
            "yaml_content": body.yaml_content,
            "adapter": adapter_ref,
            "adapter_code": "",
            "description": str(raw.get("description") or ""),
        },
    )
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")
    logger.info("Updated MinIO template: %s@%s", name, version)
    return {"name": name, "message": "Template updated"}


@router.delete("/templates/{name}")
async def delete_template(name: str) -> dict[str, Any]:
    definition = await ai_collect_store.get_template_definition(name)
    if definition is None:
        raise HTTPException(status_code=404, detail=f"Template '{name}' not found")
    if int(definition.get("task_count") or 0) > 0:
        raise HTTPException(status_code=409, detail="Template is used by workspace tasks")
    deleted = await ai_collect_store.delete_template_definition(
        name,
        str(definition["version"]),
    )
    if not deleted:
        raise HTTPException(status_code=409, detail="Template changed while deleting")
    logger.info("Deleted MinIO template: %s@%s", name, definition["version"])
    return {"name": name, "message": "Template deleted"}


def _read_yaml_safe(content: str, source: str) -> dict[str, Any]:
    data = yaml.safe_load(content)
    if not isinstance(data, dict):
        raise ValueError(f"Template must contain a YAML mapping: {source}")
    return data


def _extract_fields(raw: dict[str, Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for section in ("list_fields", "detail_fields"):
        for field in raw.get(section, []):
            if isinstance(field, dict):
                fields.append({
                    "name": field.get("name", ""),
                    "type": field.get("field_type", field.get("type", "text")),
                    "required": field.get("required", True),
                    "section": section.replace("_fields", ""),
                })
    return fields


def _build_yaml(body: TemplateCreateRequest) -> str:
    return (
        f"name: {body.name}\n"
        f'display_name: "{body.display_name or body.name}"\n'
        f'base_url: "{body.base_url}"\n'
        f"data_type: {body.data_type}\n"
        f"description: >\n  {body.description or body.name}\n"
        "\n"
        "response_type: html\n"
        'list_page: "/"\n'
        "list_request:\n"
        "  method: GET\n"
        "  headers:\n"
        '    Accept: "text/html"\n'
    )
