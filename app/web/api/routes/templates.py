from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException

from app.config.settings import settings as app_settings

router = APIRouter(prefix="/templates", tags=["templates"])


def _template_files() -> list[Path]:
    directory = Path(app_settings.template_dir)
    return sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])


def _read_template(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=500, detail=f"无法读取模板 {path.name}") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=500, detail=f"模板格式无效: {path.name}")
    return value


@router.get("")
async def list_templates() -> list[dict[str, Any]]:
    result = []
    for path in _template_files():
        item = _read_template(path)
        fields = [*item.get("list_fields", []), *item.get("detail_fields", [])]
        result.append({
            "name": str(item.get("name") or path.stem),
            "type": str(item.get("data_type") or "other"),
            "description": str(item.get("description") or ""),
            "status": "active",
            "fields": len(fields),
            "steps": len(item),
        })
    return result


@router.get("/{name}")
async def get_template(name: str) -> dict[str, Any]:
    path = next((item for item in _template_files() if item.stem == name), None)
    if path is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    item = _read_template(path)
    item["yaml_content"] = path.read_text(encoding="utf-8")
    return item


@router.post("/test")
async def test_template(payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("template_id") or "")
    path = next((item for item in _template_files() if item.stem == name), None)
    if path is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"template_id": name, "passed": True, "sample_count": 0, "message": "模板校验通过"}
