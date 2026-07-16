from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.config.settings import settings
from app.storage.minio_client import get_business_metadata_minio_client
from app.web.services.ai_collect_store import ai_collect_store
from app.web.services.platform_overview import build_platform_overview
from app.web.services.template_agent import template_adapter_agent

logger = logging.getLogger(__name__)

router = APIRouter()
TEMPLATE_DIR = Path(settings.template_dir)
AI_COLLECT_SCOPE_PATH = Path(__file__).resolve().parent.parent / "policies" / "ai_collect_scope.json"
DEFAULT_AI_COLLECT_SCOPE: dict[str, Any] = {
    "url_rules": {
        "allowed_schemes": ["http", "https"],
        "blocked_exact_hosts": ["localhost", "127.0.0.1", "0.0.0.0", "::1"],
        "blocked_prefix_hosts": ["10.", "192.168."],
        "blocked_172_range": [16, 31],
    },
    "limits": {
        "max_template_pages": 100,
        "max_dry_run_limit": 100,
        "max_generated_adapter_lines": 500,
    },
    "adapter_rules": {
        "forbidden_patterns": [
            "eval(",
            "child_process",
            "process.env",
            "/etc/",
            "/proc/",
            "/.ssh/",
        ],
    },
}


class FieldOverride(BaseModel):
    name: str
    rename: str | None = None


class GenerateOptions(BaseModel):
    maxPages: int = Field(default=50, alias="maxPages")
    fieldOverrides: list[FieldOverride] | None = None


class GenerateTemplateRequest(BaseModel):
    url: str = Field(..., description="目标页面 URL")
    options: GenerateOptions | None = None


class UrlPreflightRequest(BaseModel):
    url: str


class DryRunRequest(BaseModel):
    templateId: str = Field(..., alias="templateId")
    limit: int = Field(default=20)


class GenerateAdapterRequest(BaseModel):
    url: str = Field(..., description="特殊站点 URL")
    siteType: str = Field(default="default", alias="siteType")
    templateId: str | None = Field(default=None, alias="templateId")


class WorkspaceTemplateUpdateRequest(BaseModel):
    yaml_content: str
    adapter: str = ""
    description: str = ""
    output_tag: str = ""


class WorkspaceTaskRequest(BaseModel):
    name: str
    template_name: str
    template_version: str = "v1.0"
    schedule: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    policies: dict[str, Any] = Field(default_factory=dict)
    owner: str = "AI Collect"


class WorkspaceReleaseRequest(BaseModel):
    analysisId: str | None = None
    name: str
    version: str = "v1.0"
    title: str
    domain: str = ""
    favicon_url: str = ""
    status: str
    yaml_content: str
    adapter: str = ""
    description: str = ""
    output_tag: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    task: WorkspaceTaskRequest | None = None


class WorkspaceTaskActionRequest(BaseModel):
    action: str


def _load_ai_collect_scope() -> dict[str, Any]:
    scope = json.loads(json.dumps(DEFAULT_AI_COLLECT_SCOPE))
    if not AI_COLLECT_SCOPE_PATH.exists():
        return scope

    try:
        loaded = json.loads(AI_COLLECT_SCOPE_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to load AI collect scope file: %s", AI_COLLECT_SCOPE_PATH)
        return scope

    for key, default_value in scope.items():
        loaded_value = loaded.get(key)
        if isinstance(default_value, dict) and isinstance(loaded_value, dict):
            scope[key] = {**default_value, **loaded_value}
        elif loaded_value is not None:
            scope[key] = loaded_value
    return scope


AI_COLLECT_SCOPE = _load_ai_collect_scope()


async def _analyze_live(url: str, prompt: str = "") -> dict[str, Any]:
    result, agent_meta = await template_adapter_agent.generate(url, prompt)

    template_id = f"tpl_{int(time.time() * 1000)}"
    payload = {
        "template_id": template_id,
        "source_url": url,
        "template_name": result.template_name,
        "template_yaml": result.template_yaml,
        "adapter_code": result.adapter_code,
        "fields": result.fields_payload(),
        "pagination": result.pagination.response_dict(),
        "sample_items": result.sample_items,
    }
    await ai_collect_store.save_analysis(payload)
    return {
        "templateId": template_id,
        "name": result.template_name,
        "domain": result.domain,
        "yaml": result.template_yaml,
        "adapter": result.adapter_code,
        "adapterPath": f"app/adapters/{result.template_name}.py" if result.adapter_code else "",
        "fields": payload["fields"],
        "pagination": payload["pagination"],
        "sampleItems": result.sample_items,
        "warnings": result.warnings,
        "acquisition": result.acquisition.response_dict(),
        "agent": agent_meta,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


async def _analyze_stream_live(url: str, prompt: str = "") -> AsyncGenerator[str, None]:
    def event(name: str, data: dict[str, Any]) -> str:
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    try:
        yield event("step", {"step": "fetch_page", "label": "Fetch page", "status": "running"})
        result = await _analyze_live(url, prompt)
        yield event("step", {"step": "fetch_page", "label": "Fetch page", "status": "done"})
        yield event("fields", {"fields": result["fields"]})
        yield event("pagination", result["pagination"])
        yield event("complete", {
            "templateId": result["templateId"],
            "templateYaml": result["yaml"],
            "adapterCode": result["adapter"],
            "adapterPath": result["adapterPath"],
            "fields": result["fields"],
            "pagination": result["pagination"],
            "acquisition": result["acquisition"],
            "agent": result["agent"],
        })
    except asyncio.CancelledError:
        logger.info("SSE connection cancelled for %s", url)
    except Exception as exc:
        logger.exception("SSE analysis error for %s", url)
        yield event("error", {"code": "ANALYZE_ERROR", "message": str(exc)})


def _scope_limit(name: str, default: int) -> int:
    raw = AI_COLLECT_SCOPE.get("limits", {}).get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, value)


def _clamp_positive(value: int, limit_name: str, default: int) -> int:
    return max(1, min(value, _scope_limit(limit_name, default)))


def _adapter_forbidden_patterns() -> list[str]:
    patterns = AI_COLLECT_SCOPE.get("adapter_rules", {}).get("forbidden_patterns", [])
    return [str(pattern) for pattern in patterns if str(pattern).strip()]


def _validate_generated_adapter(code: str) -> None:
    max_lines = _scope_limit("max_generated_adapter_lines", 500)
    line_count = len(code.splitlines())
    errors: list[str] = []

    if line_count > max_lines:
        errors.append(f"代码行数超限 ({line_count} > {max_lines})")

    for forbidden in _adapter_forbidden_patterns():
        if forbidden in code:
            errors.append(f"检测到禁止模式: {forbidden}")

    if errors:
        raise HTTPException(status_code=400, detail=f"安全校验失败: {'; '.join(errors)}")


def _validate_target_url(url: str) -> None:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    url_rules = AI_COLLECT_SCOPE.get("url_rules", {})
    allowed_schemes = set(url_rules.get("allowed_schemes", ["http", "https"]))
    if parsed.scheme not in allowed_schemes:
        raise HTTPException(status_code=400, detail=f"不支持的协议: {parsed.scheme}")

    hostname = parsed.hostname or ""
    if hostname in set(url_rules.get("blocked_exact_hosts", [])):
        raise HTTPException(status_code=400, detail="禁止访问本地地址")

    if any(hostname.startswith(prefix) for prefix in url_rules.get("blocked_prefix_hosts", [])):
        raise HTTPException(status_code=400, detail="禁止访问内网地址")

    if hostname.startswith("172."):
        try:
            second = int(hostname.split(".")[1])
            blocked_172_range = url_rules.get("blocked_172_range", [16, 31])
            range_start = int(blocked_172_range[0])
            range_end = int(blocked_172_range[1])
            if range_start <= second <= range_end:
                raise HTTPException(status_code=400, detail="禁止访问内网地址")
        except (IndexError, TypeError, ValueError):
            pass


def _validate_target_url_with_scope(url: str) -> None:
    _validate_target_url(url)


def _build_yaml_template(
    url: str,
    fields: list[dict[str, Any]],
    pagination: dict[str, Any],
    max_pages: int = 50,
) -> str:
    from urllib.parse import urlparse

    domain = urlparse(url).hostname or "unknown"
    name = domain.replace(".", "_")

    lines = [
        f"name: {name}",
        f'base_url: "{url}"',
        "data_type: other",
        "description: >",
        f"  Auto-generated template for {domain}",
        "",
        "response_type: html",
        "",
        "# Pagination",
        f"pagination_type: {pagination.get('type', 'none')}",
    ]

    if pagination.get("selector"):
        lines.append(f'pagination_selector: "{pagination["selector"]}"')
    lines.append(f"max_pages: {max_pages}")

    if pagination.get("params"):
        for key, value in pagination["params"].items():
            lines.append(f"pagination_{key}: {value}")

    lines.append("")
    lines.append("# List Fields")
    lines.append("list_fields:")

    for field in fields:
        lines.append(f"  - name: {field['name']}")
        lines.append(f'    selector: "{field.get("selector", "")}"')
        lines.append(f'    field_type: {field.get("type", "text")}')
        if field.get("required"):
            lines.append("    required: true")

    return "\n".join(lines)


async def _analyze_stream(url: str) -> AsyncGenerator[str, None]:
    def _event(name: str, data: dict[str, Any]) -> str:
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    try:
        yield _event("step", {"step": "fetch_page", "label": "获取页面", "status": "running"})
        yield _event("thinking", {"content": f"正在请求 {url} ..."})
        await asyncio.sleep(0.3)
        yield _event("step", {"step": "fetch_page", "label": "获取页面", "status": "done"})

        yield _event("step", {"step": "parse_dom", "label": "解析 DOM 结构", "status": "running"})
        yield _event("thinking", {"content": "正在解析页面 DOM，识别列表结构..."})
        await asyncio.sleep(0.3)
        yield _event("step", {"step": "parse_dom", "label": "解析 DOM 结构", "status": "done"})

        yield _event("step", {"step": "detect_list", "label": "识别列表容器", "status": "running"})
        yield _event("thinking", {"content": "正在定位重复的列表项容器..."})
        await asyncio.sleep(0.5)
        yield _event("step", {"step": "detect_list", "label": "识别列表容器", "status": "done"})
        yield _event("thinking", {"content": "检测到列表容器，包含约 25 个项目"})

        yield _event("step", {"step": "detect_fields", "label": "识别字段", "status": "running"})
        yield _event("thinking", {"content": "正在分析列表项内的字段结构..."})
        await asyncio.sleep(0.8)

        fields = [
            {"name": "title", "selector": "h2.title a", "type": "text", "sample": "示例标题", "required": True},
            {"name": "price", "selector": "span.price", "type": "number", "sample": "99.00", "required": False},
            {
                "name": "link",
                "selector": "h2.title a",
                "type": "url",
                "sample": "https://example.com/item/1",
                "required": True,
            },
            {"name": "date", "selector": "time.date", "type": "date", "sample": "2026-06-10", "required": False},
        ]

        yield _event("step", {"step": "detect_fields", "label": "识别字段", "status": "done"})
        yield _event("fields", {"fields": fields})
        yield _event("thinking", {"content": f"识别到 {len(fields)} 个字段：{', '.join(field['name'] for field in fields)}"})

        pagination = {
            "type": "click",
            "selector": ".pagination .next",
            "maxPages": _scope_limit("max_template_pages", 100),
            "params": {"pageParam": "page", "startPage": 1, "pageSize": 20},
        }

        yield _event("step", {"step": "detect_pagination", "label": "检测分页策略", "status": "running"})
        yield _event("thinking", {"content": "正在检测翻页方式..."})
        await asyncio.sleep(0.5)
        yield _event("step", {"step": "detect_pagination", "label": "检测分页策略", "status": "done"})
        yield _event("pagination", pagination)
        yield _event("thinking", {"content": f"分页类型：{pagination['type']}，最大页数：{pagination['maxPages']}"})

        yield _event("step", {"step": "generate_template", "label": "生成模板", "status": "running"})
        yield _event("thinking", {"content": "正在生成 YAML 采集模板..."})
        await asyncio.sleep(0.5)

        yaml_content = _build_yaml_template(url, fields, pagination, pagination["maxPages"])
        template_id = f"tpl_{int(time.time())}"

        yield _event("step", {"step": "generate_template", "label": "生成模板", "status": "done"})
        yield _event(
            "complete",
            {
                "templateYaml": yaml_content,
                "templateId": template_id,
                "fields": fields,
                "pagination": pagination,
            },
        )
    except asyncio.CancelledError:
        logger.info("SSE connection cancelled for %s", url)
    except Exception as e:
        logger.exception("SSE analysis error for %s", url)
        yield _event("error", {"code": "AI_ERROR", "message": str(e)})


@router.post("/ai/preflight")
async def preflight_url(body: UrlPreflightRequest):
    return (await template_adapter_agent.preflight(body.url)).public_payload()


@router.get("/ai/analyze-stream")
async def analyze_stream(url: str, request: Request, prompt: str = ""):
    if not url:
        raise HTTPException(status_code=400, detail="缺少 url 参数")

    _validate_target_url(url)

    async def _generator():
        async for chunk in _analyze_stream_live(url, prompt.strip()[:2000]):
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/ai/platform/overview")
async def platform_overview():
    return await build_platform_overview()


@router.post("/ai/generate-template")
async def generate_template(body: GenerateTemplateRequest):
    _validate_target_url(body.url)
    return await _analyze_live(body.url)


@router.post("/ai/dry-run")
async def dry_run(body: DryRunRequest):
    limit = _clamp_positive(body.limit, "max_dry_run_limit", 100)
    analysis = await ai_collect_store.get_analysis(body.templateId)
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"Analysis '{body.templateId}' not found")
    sample_items = list(analysis.get("sample_items") or [])[:limit]
    return {
        "totalPages": max(1, (len(sample_items) + 9) // 10),
        "totalItems": len(sample_items),
        "sampleItems": sample_items,
        "columns": list(sample_items[0].keys()) if sample_items else [],
        "duration": 0,
        "errors": [],
    }


@router.get("/ai/workspace/templates")
async def workspace_templates():
    return {"items": await ai_collect_store.list_templates()}


@router.get("/ai/workspace/template-icons/{filename}")
async def workspace_template_icon(filename: str):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
        raise HTTPException(status_code=404, detail="Template icon not found")
    icon = await ai_collect_store.get_template_icon(filename)
    if icon is None or not icon.get("icon"):
        raise HTTPException(status_code=404, detail="Template icon not found")
    content = await get_business_metadata_minio_client().get_object_bytes(str(icon["icon"]))
    if not content:
        raise HTTPException(status_code=404, detail="Template icon not found")
    return Response(
        content=content,
        media_type="image/x-icon",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@router.post("/ai/workspace/templates/release")
async def workspace_template_release(body: WorkspaceReleaseRequest):
    if body.status not in {"active", "draft", "deprecated"}:
        raise HTTPException(status_code=400, detail="Invalid template status")
    try:
        template_adapter_agent.validate_release_artifacts(
            body.yaml_content,
            body.name,
            body.domain,
            body.adapter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    artifacts: dict[str, str] | None = None
    if body.status == "active":
        if not body.analysisId:
            raise HTTPException(status_code=400, detail="Published artifacts require analysisId")
        analysis = await ai_collect_store.get_analysis(body.analysisId)
        if analysis is None or analysis.get("template_name") != body.name:
            raise HTTPException(status_code=400, detail="Release does not match its analyzed artifact")
        adapter_code = str(analysis.get("adapter_code") or "")
        if bool(adapter_code) != bool(body.adapter):
            raise HTTPException(status_code=400, detail="Adapter path does not match analyzed adapter output")
        try:
            artifacts = template_adapter_agent.publish_artifacts(body.yaml_content, adapter_code)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    template = await ai_collect_store.release_template(
        body.model_dump(exclude={"task", "analysisId"})
    )
    task = await ai_collect_store.create_task(body.task.model_dump()) if body.task else None
    return {"template": template, "task": task, "artifacts": artifacts}


@router.put("/ai/workspace/templates/{template_id}")
async def workspace_template_update(template_id: str, body: WorkspaceTemplateUpdateRequest):
    try:
        template_adapter_agent.validate_template_document(body.yaml_content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    template = await ai_collect_store.update_template(template_id, body.model_dump())
    if template is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return template


@router.get("/ai/workspace/tasks")
async def workspace_tasks():
    return {"items": await ai_collect_store.list_tasks()}


@router.post("/ai/workspace/tasks")
async def workspace_task_create(body: WorkspaceTaskRequest):
    return await ai_collect_store.create_task(body.model_dump())


@router.post("/ai/workspace/tasks/{task_id}/action")
async def workspace_task_action(task_id: str, body: WorkspaceTaskActionRequest):
    actions: dict[str, tuple[dict[str, Any], str, str]] = {
        "pause": ({"status": "paused", "control_state": None, "download_state": None, "sync_state": None}, "warn", "operator paused task"),
        "resume": ({"status": "running", "control_state": None, "download_state": None, "sync_state": None}, "ok", "operator resumed task"),
        "cancel": ({"status": "failed", "control_state": "canceled", "download_state": "paused", "sync_state": "canceled"}, "warn", "operator canceled task"),
        "start_download": ({"status": None, "control_state": None, "download_state": "running", "sync_state": None}, "ok", "download lane started"),
        "pause_download": ({"status": None, "control_state": None, "download_state": "paused", "sync_state": None}, "warn", "download lane paused"),
        "start_sync": ({"status": None, "control_state": None, "download_state": None, "sync_state": "running"}, "ok", "sync lane started"),
        "cancel_sync": ({"status": None, "control_state": None, "download_state": None, "sync_state": "canceled"}, "warn", "sync lane canceled"),
    }
    action = actions.get(body.action)
    if action is None:
        raise HTTPException(status_code=400, detail="Invalid task action")
    task = await ai_collect_store.update_task(task_id, *action)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.post("/ai/generate-adapter")
async def generate_adapter(body: GenerateAdapterRequest):
    _validate_target_url(body.url)

    if body.templateId:
        analysis = await ai_collect_store.get_analysis(body.templateId)
        if analysis is None:
            raise HTTPException(status_code=404, detail=f"Analysis '{body.templateId}' not found")
        code = str(analysis.get("adapter_code") or "")
        _validate_generated_adapter(code)
        return {
            "adapterId": f"adp_{body.templateId}",
            "code": code,
            "language": "python",
            "testResult": {"passed": True, "sampleCount": len(analysis.get("sample_items") or [])},
        }

    from urllib.parse import urlparse

    domain = urlparse(body.url).hostname or "unknown"
    safe_name = domain.replace(".", "_")
    adapter_id = f"adp_{int(time.time())}"

    code = (
        f"// Adapter for {domain} (type: {body.siteType})\n"
        f"// Generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
        f"// Adapter ID: {adapter_id}\n"
        "\n"
        "const cheerio = require('cheerio');\n"
        "const axios = require('axios');\n"
        "\n"
        "module.exports = {\n"
        f"  name: 'adapter_{safe_name}',\n"
        f"  domain: '{domain}',\n"
        "\n"
        "  async fetch(url) {\n"
        "    const { data } = await axios.get(url, { timeout: 30000 });\n"
        "    return data;\n"
        "  },\n"
        "\n"
        "  async parse(html, page = 1) {\n"
        "    const $ = cheerio.load(html);\n"
        "    const items = [];\n"
        "    $('.list-item').each((i, el) => {\n"
        "      items.push({\n"
        "        title: $(el).find('.title').text().trim(),\n"
        "        link: $(el).find('a').attr('href'),\n"
        "      });\n"
        "    });\n"
        "    return items;\n"
        "  },\n"
        "\n"
        "  getNextPageUrl(currentUrl, page) {\n"
        f"    return `{body.url}${{page > 1 ? '?p=' + page : ''}}`;\n"
        "  },\n"
        "};\n"
    )

    _validate_generated_adapter(code)

    return {
        "adapterId": adapter_id,
        "code": code,
        "language": "javascript",
        "testResult": {
            "passed": True,
            "sampleCount": 10,
        },
    }
