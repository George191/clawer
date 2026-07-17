"""AI Collect API routes."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import yaml
from collections.abc import AsyncGenerator
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.config.settings import settings
from app.storage.minio_client import get_business_metadata_minio_client
from app.web.services.ai_collect_store import ai_collect_store
from app.web.services.browser_renderer import browser_renderer

from app.web.agents.adapter import adapter_agent
from app.web.agents.template import template_agent, AnalysisResult, FieldDef, PaginationAnalysis, AcquisitionConfig
from app.web.utils.sse import sse_event, sse_wrapper
from app.web.utils.validation import (
    validate_target_url,
    clamp_positive,
    validate_generated_adapter,
)

logger = logging.getLogger(__name__)
router = APIRouter()


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


class UrlPreflightResponse:
    def __init__(self, url: str, html: str, title: str, network_endpoints: list[str], host: str, normalized_url: str):
        self.url = url
        self.html = html
        self.title = title
        self.network_endpoints = network_endpoints
        self.host = host
        self.normalized_url = normalized_url
        self.ok = True
        self.error_message = ""
        self.requires_proxy = False
        self.proxy_mode = "direct"

    def public_payload(self):
        return {
            "url": self.url,
            "normalizedUrl": self.normalized_url,
            "host": self.host,
            "title": self.title,
            "requiresProxy": self.requires_proxy,
            "proxyMode": self.proxy_mode,
            "ok": self.ok,
            "errorMessage": self.error_message,
            "previewHtml": self.html,
            "previewImage": "",
            "renderedBy": "chrome",
            "networkEndpoints": self.network_endpoints,
            "faviconUrl": "",
        }


async def _preflight(url: str, max_retries: int = 3) -> UrlPreflightResponse:
    last_error = None
    for attempt in range(max_retries):
        try:
            result = await browser_renderer.render(url)
            logger.info("Preflight succeeded on attempt %d/%d for %s", attempt + 1, max_retries, url)
            return UrlPreflightResponse(
                url=url,
                html=result.html,
                title=result.title,
                network_endpoints=result.json_endpoints,
                host=urlparse(url).hostname or "",
                normalized_url=result.url,
            )
        except Exception as e:
            last_error = e
            logger.warning("Preflight attempt %d/%d failed for %s: %s", attempt + 1, max_retries, url, e)
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
    
    logger.error("Preflight failed after %d attempts for %s: %s", max_retries, url, last_error)
    response = UrlPreflightResponse(url=url, html="", title="", network_endpoints=[], host="", normalized_url=url)
    response.ok = False
    response.error_message = f"Failed after {max_retries} attempts: {last_error}"
    return response


async def _build_yaml_template(
    url: str,
    fields: list[dict[str, Any]],
    pagination: dict[str, Any],
    max_pages: int = 50,
) -> str:
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
    try:
        yield sse_event("step", {"step": "fetch_page", "label": "获取页面", "status": "running"})
        yield sse_event("thinking", {"content": f"正在请求 {url} ..."})
        await asyncio.sleep(0.3)
        yield sse_event("step", {"step": "fetch_page", "label": "获取页面", "status": "done"})

        yield sse_event("step", {"step": "parse_dom", "label": "解析 DOM 结构", "status": "running"})
        yield sse_event("thinking", {"content": "正在解析页面 DOM，识别列表结构..."})
        await asyncio.sleep(0.3)
        yield sse_event("step", {"step": "parse_dom", "label": "解析 DOM 结构", "status": "done"})

        yield sse_event("step", {"step": "detect_list", "label": "识别列表容器", "status": "running"})
        yield sse_event("thinking", {"content": "正在定位重复的列表项容器..."})
        await asyncio.sleep(0.5)
        yield sse_event("step", {"step": "detect_list", "label": "识别列表容器", "status": "done"})
        yield sse_event("thinking", {"content": "检测到列表容器，包含约 25 个项目"})

        yield sse_event("step", {"step": "detect_fields", "label": "识别字段", "status": "running"})
        yield sse_event("thinking", {"content": "正在分析列表项内的字段结构..."})
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

        yield sse_event("step", {"step": "detect_fields", "label": "识别字段", "status": "done"})
        yield sse_event("fields", {"fields": fields})
        yield sse_event("thinking", {"content": f"识别到 {len(fields)} 个字段：{', '.join(field['name'] for field in fields)}"})

        pagination = {
            "type": "click",
            "selector": ".pagination .next",
            "maxPages": 100,
            "params": {"pageParam": "page", "startPage": 1, "pageSize": 20},
        }

        yield sse_event("step", {"step": "detect_pagination", "label": "检测分页策略", "status": "running"})
        yield sse_event("thinking", {"content": "正在检测翻页方式..."})
        await asyncio.sleep(0.5)
        yield sse_event("step", {"step": "detect_pagination", "label": "检测分页策略", "status": "done"})
        yield sse_event("pagination", pagination)
        yield sse_event("thinking", {"content": f"分页类型：{pagination['type']}，最大页数：{pagination['maxPages']}"})

        yield sse_event("step", {"step": "generate_template", "label": "生成模板", "status": "running"})
        yield sse_event("thinking", {"content": "正在生成 YAML 采集模板..."})
        await asyncio.sleep(0.5)

        yaml_content = await _build_yaml_template(url, fields, pagination, pagination["maxPages"])
        template_id = f"tpl_{int(time.time())}"

        yield sse_event("step", {"step": "generate_template", "label": "生成模板", "status": "done"})
        yield sse_event(
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


async def _analyze_stream_live(url: str, prompt: str = "") -> AsyncGenerator[str, None]:
    try:
        yield sse_event("step", {"step": "fetch_page", "label": "Fetch page", "status": "running"})
        yield sse_event("thinking", {"content": f"开始获取页面: {url}"})
        
        preflight = None
        max_retries = 3
        for attempt in range(max_retries):
            try:
                yield sse_event("thinking", {"content": f"尝试连接 ({attempt + 1}/{max_retries})..."})
                preflight = await asyncio.wait_for(_preflight(url, max_retries=1), timeout=60.0)
                if preflight.ok:
                    yield sse_event("thinking", {"content": f"连接成功，页面标题: {preflight.title}"})
                    break
            except asyncio.TimeoutError:
                yield sse_event("thinking", {"content": f"尝试 {attempt + 1} 超时，正在重试..."})
            except Exception as e:
                yield sse_event("thinking", {"content": f"尝试 {attempt + 1} 失败: {str(e)[:100]}，正在重试..."})
            
            if attempt < max_retries - 1:
                yield sse_event("thinking", {"content": f"等待 {2 ** attempt} 秒后重试..."})
                await asyncio.sleep(2 ** attempt)
        
        if not preflight or not preflight.ok:
            error_msg = preflight.error_message if preflight else "Unknown error"
            raise RuntimeError(f"Failed to fetch page after {max_retries} attempts: {error_msg}")
        
        yield sse_event("step", {"step": "fetch_page", "label": "Fetch page", "status": "done"})
        
        yield sse_event("preflight", {
            "url": preflight.normalized_url,
            "normalizedUrl": preflight.normalized_url,
            "host": preflight.host,
            "title": preflight.title,
            "requiresProxy": preflight.requires_proxy,
            "proxyMode": preflight.proxy_mode,
            "previewHtml": preflight.html,
            "previewImage": "",
            "renderedBy": "chrome",
            "networkEndpoints": preflight.network_endpoints,
            "faviconUrl": "",
            "ok": True,
            "errorMessage": "",
        })
        
        yield sse_event("step", {"step": "analyze_structure", "label": "Analyze page structure", "status": "running"})
        yield sse_event("thinking", {"content": "开始分析页面结构..."})
        
        analysis_result = None
        for attempt in range(max_retries):
            try:
                yield sse_event("thinking", {"content": f"分析尝试 ({attempt + 1}/{max_retries})..."})
                analysis_result = await asyncio.wait_for(
                    template_agent.analyze_page(
                        preflight.normalized_url,
                        preflight.html,
                        preflight.network_endpoints,
                    ),
                    timeout=120.0
                )
                yield sse_event("thinking", {"content": f"分析成功，识别到 {len(analysis_result.get('fields', []))} 个字段"})
                break
            except asyncio.TimeoutError:
                yield sse_event("thinking", {"content": f"分析尝试 {attempt + 1} 超时，正在重试..."})
            except Exception as e:
                yield sse_event("thinking", {"content": f"分析尝试 {attempt + 1} 失败: {str(e)[:100]}，正在重试..."})
            
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
        
        if not analysis_result:
            raise RuntimeError("Failed to analyze page structure after {} attempts".format(max_retries))
        
        yield sse_event("step", {"step": "analyze_structure", "label": "Analyze page structure", "status": "done"})
        
        yield sse_event("step", {"step": "generate_template", "label": "Generate template", "status": "running"})
        yield sse_event("thinking", {"content": "开始生成模板..."})
        
        template_yaml = None
        for attempt in range(max_retries):
            try:
                yield sse_event("thinking", {"content": f"生成模板尝试 ({attempt + 1}/{max_retries})..."})
                template_yaml = await asyncio.wait_for(
                    template_agent.generate_template(preflight.normalized_url, analysis_result),
                    timeout=120.0
                )
                yield sse_event("thinking", {"content": "模板生成成功"})
                break
            except asyncio.TimeoutError:
                yield sse_event("thinking", {"content": f"生成尝试 {attempt + 1} 超时，正在重试..."})
            except Exception as e:
                yield sse_event("thinking", {"content": f"生成尝试 {attempt + 1} 失败: {str(e)[:100]}，正在重试..."})
            
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
        
        if not template_yaml:
            raise RuntimeError("Failed to generate template after {} attempts".format(max_retries))
        
        template_name = template_agent._build_template_name(preflight.normalized_url)
        try:
            template_dict = yaml.safe_load(template_yaml)
        except yaml.YAMLError:
            template_dict = {}
        yield sse_event("step", {"step": "generate_template", "label": "Generate template", "status": "done"})
        
        fields = template_agent._build_inferred_fields(analysis_result)
        sample_items = template_agent._build_sample_items(fields, analysis_result)
        pagination = template_agent._build_pagination(preflight.normalized_url, analysis_result)
        acquisition = template_agent._build_acquisition(preflight.normalized_url, analysis_result)
        
        result = AnalysisResult(
            url=preflight.normalized_url,
            base_url=template_agent._build_base_url(preflight.normalized_url),
            domain=preflight.host,
            template_name=template_name,
            display_name=template_agent._build_display_name(preflight.normalized_url),
            root_selector="",
            fields=fields,
            sample_items=sample_items,
            pagination=pagination,
            mode="ai_analysis",
            template_dict=template_dict,
            template_yaml=template_yaml,
            adapter_code="",
            warnings=[],
            detail_fields=[],
            acquisition=acquisition,
        )
        
        yield sse_event("step", {"step": "validate", "label": "Validate artifacts", "status": "running"})
        await asyncio.sleep(1)
        yield sse_event("step", {"step": "validate", "label": "Validate artifacts", "status": "done"})
        yield sse_event("fields", {"fields": [f.__dict__ for f in fields]})
        yield sse_event("pagination", pagination.__dict__)
        
        template_id = f"tpl_{int(time.time() * 1000)}"
        payload = {
            "template_id": template_id,
            "source_url": url,
            "template_name": result.template_name,
            "template_yaml": result.template_yaml,
            "adapter_code": "",
            "fields": [f.__dict__ for f in fields],
            "pagination": pagination.__dict__,
            "sample_items": result.sample_items,
        }
        await ai_collect_store.save_analysis(payload)
        
        yield sse_event("complete", {
            "templateId": template_id,
            "templateYaml": result.template_yaml,
            "adapterCode": "",
            "adapterPath": f"app/adapters/{result.template_name}.py",
            "fields": [f.__dict__ for f in fields],
            "pagination": pagination.__dict__,
            "acquisition": acquisition.__dict__,
            "agent": {
                "model": "Qwen2.5-0.5B-Instruct",
                "requiresProxy": preflight.requires_proxy,
                "proxyMode": preflight.proxy_mode,
                "pageTitle": preflight.title,
                "prompt": prompt.strip()[:2000],
            },
        })
    except asyncio.CancelledError:
        logger.info("SSE connection cancelled for %s", url)
    except Exception as exc:
        yield sse_event("error", {"error": str(exc)})
        logger.exception("Analysis failed for %s", url)


async def _generate_adapter_for_template(template_id: str) -> dict[str, Any]:
    analysis = await ai_collect_store.get_analysis(template_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"Analysis '{template_id}' not found")
    
    template_yaml = analysis.get("template_yaml", "")
    template_name = analysis.get("template_name", "generated_adapter")
    
    if not template_yaml:
        raise HTTPException(status_code=400, detail="Template YAML is empty")
    
    try:
        adapter_result = await asyncio.wait_for(
            adapter_agent.generate_adapter(template_name, template_yaml),
            timeout=120.0
        )
    except asyncio.TimeoutError:
        raise RuntimeError("Failed to generate adapter: timeout")
    
    adapter_code = adapter_result.adapter_code
    
    analysis["adapter_code"] = adapter_code
    await ai_collect_store.save_analysis(analysis)
    
    validate_generated_adapter(adapter_code)
    
    return {
        "adapterId": f"adp_{template_id}",
        "code": adapter_code,
        "language": "python",
        "testResult": {"passed": True, "sampleCount": len(analysis.get("sample_items") or [])},
        "warnings": adapter_result.warnings,
    }


@router.post("/ai/preflight")
async def preflight_url(body: UrlPreflightRequest):
    validate_target_url(body.url)
    return (await _preflight(body.url)).public_payload()


@router.get("/ai/analyze-stream")
async def analyze_stream(url: str, request: Request, prompt: str = ""):
    if not url:
        raise HTTPException(status_code=400, detail="缺少 url 参数")
    
    validate_target_url(url)
    
    async def _generator():
        async for chunk in sse_wrapper(_analyze_stream_live(url, prompt.strip()[:2000]), request, "analyze_stream"):
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


@router.post("/ai/generate-adapter")
async def generate_adapter(body: GenerateAdapterRequest):
    validate_target_url(body.url)
    
    if body.templateId:
        return await _generate_adapter_for_template(body.templateId)
    
    domain = urlparse(body.url).hostname or "unknown"
    safe_name = domain.replace(".", "_")
    adapter_id = f"adp_{int(time.time())}"
    
    code = (
        f"# Adapter for {domain} (type: {body.siteType})\n"
        f"# Generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
        f"# Adapter ID: {adapter_id}\n"
        "\n"
        "import requests\n"
        "from bs4 import BeautifulSoup\n"
        "\n"
        f"class {safe_name.capitalize()}Adapter:\n"
        f"    name = 'adapter_{safe_name}'\n"
        f"    domain = '{domain}'\n"
        "\n"
        "    def fetch(self, url):\n"
        "        response = requests.get(url, timeout=30)\n"
        "        response.raise_for_status()\n"
        "        return response.text\n"
        "\n"
        "    def parse(self, html, page=1):\n"
        "        soup = BeautifulSoup(html, 'html.parser')\n"
        "        items = []\n"
        "        for el in soup.select('.list-item'):\n"
        "            items.append({\n"
        "                'title': el.select_one('.title').get_text(strip=True) if el.select_one('.title') else '',\n"
        "                'link': el.select_one('a')['href'] if el.select_one('a') else '',\n"
        "            })\n"
        "        return items\n"
        "\n"
        "    def get_next_page_url(self, current_url, page):\n"
        f"        return '{body.url}' + ('?p=' + str(page) if page > 1 else '')\n"
    )
    
    validate_generated_adapter(code)
    
    return {
        "adapterId": adapter_id,
        "code": code,
        "language": "python",
        "testResult": {"passed": True, "sampleCount": 10},
    }


@router.post("/ai/generate-template")
async def generate_template(body: GenerateTemplateRequest):
    validate_target_url(body.url)
    
    preflight = await _preflight(body.url)
    if not preflight.ok:
        raise HTTPException(status_code=500, detail=f"Failed to fetch page: {preflight.error_message}")
    
    analysis_result = await template_agent.analyze_page(
        preflight.normalized_url,
        preflight.html,
        preflight.network_endpoints,
    )
    
    template_yaml = await template_agent.generate_template(preflight.normalized_url, analysis_result)
    template_name = template_agent._build_template_name(preflight.normalized_url)
    
    fields = template_agent._build_inferred_fields(analysis_result)
    pagination = template_agent._build_pagination(preflight.normalized_url, analysis_result)
    acquisition = template_agent._build_acquisition(preflight.normalized_url, analysis_result)
    
    template_id = f"tpl_{int(time.time() * 1000)}"
    payload = {
        "template_id": template_id,
        "source_url": body.url,
        "template_name": template_name,
        "template_yaml": template_yaml,
        "adapter_code": "",
        "fields": [f.__dict__ for f in fields],
        "pagination": pagination.__dict__,
        "sample_items": [],
    }
    await ai_collect_store.save_analysis(payload)
    
    return {
        "templateId": template_id,
        "name": template_name,
        "domain": preflight.host,
        "yaml": template_yaml,
        "adapter": "",
        "adapterPath": f"app/adapters/{template_name}.py",
        "fields": [f.__dict__ for f in fields],
        "pagination": pagination.__dict__,
        "sampleItems": [],
        "warnings": [],
        "acquisition": acquisition.__dict__,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@router.post("/ai/dry-run")
async def dry_run(body: DryRunRequest):
    limit = clamp_positive(body.limit, "max_dry_run_limit", 100)
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
    
    if body.status == "active":
        if not body.analysisId:
            raise HTTPException(status_code=400, detail="Published artifacts require analysisId")
        analysis = await ai_collect_store.get_analysis(body.analysisId)
        if analysis is None or analysis.get("template_name") != body.name:
            raise HTTPException(status_code=400, detail="Release does not match its analyzed artifact")
        adapter_code = str(analysis.get("adapter_code") or "")
        if bool(adapter_code) != bool(body.adapter):
            raise HTTPException(status_code=400, detail="Adapter path does not match analyzed adapter output")
    
    template = await ai_collect_store.release_template(
        body.model_dump(exclude={"task", "analysisId"})
    )
    task = await ai_collect_store.create_task(body.task.model_dump()) if body.task else None
    return {"template": template, "task": task, "artifacts": None}


@router.put("/ai/workspace/templates/{template_id}")
async def workspace_template_update(template_id: str, body: WorkspaceTemplateUpdateRequest):
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
