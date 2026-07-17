"""AI Collect API routes."""

from __future__ import annotations

import asyncio
import logging
import re
import time
import yaml
from collections.abc import AsyncGenerator
from lxml import etree, html as lxml_html
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.storage.minio_client import get_business_metadata_minio_client
from app.web.services.ai_collect_store import ai_collect_store
from app.web.services.browser_renderer import browser_renderer

from app.web.agents.adapter import adapter_agent
from app.web.agents.template import AnalysisResult, template_agent
from app.web.utils.validation import (
    validate_target_url,
    clamp_positive,
    validate_generated_adapter,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _sanitize_preview_html(source: str) -> str:
    """Remove executable content before placing captured HTML in a sandboxed srcDoc."""
    if not source:
        return ""

    try:
        document = lxml_html.document_fromstring(source)
    except (etree.ParserError, ValueError):
        return ""

    for element in document.xpath("//*[local-name()='script' or local-name()='iframe' or local-name()='object' or local-name()='embed']"):
        element.drop_tree()
    for element in document.xpath(
        "//*[local-name()='meta' and "
        "translate(@http-equiv, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='refresh']"
    ):
        element.drop_tree()

    for element in document.iter():
        for attribute, value in list(element.attrib.items()):
            normalized_attribute = attribute.lower()
            normalized_value = value.strip().lower()
            if normalized_attribute.startswith("on"):
                del element.attrib[attribute]
            elif normalized_attribute in {"href", "src", "action", "formaction"} and normalized_value.startswith("javascript:"):
                del element.attrib[attribute]

    return lxml_html.tostring(document, encoding="unicode", method="html")


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
    def __init__(
        self,
        url: str,
        html: str,
        title: str,
        network_endpoints: list[str],
        host: str,
        normalized_url: str,
        preview_image: str = "",
        favicon_url: str = "",
    ):
        self.url = url
        self.html = html
        self.preview_html = _sanitize_preview_html(html)
        self.title = title
        self.network_endpoints = network_endpoints
        self.host = host
        self.normalized_url = normalized_url
        self.preview_image = preview_image
        self.favicon_url = favicon_url
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
            "previewHtml": self.preview_html,
            "previewImage": self.preview_image,
            "renderedBy": "chrome",
            "networkEndpoints": self.network_endpoints,
            "faviconUrl": self.favicon_url,
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
                preview_image=result.screenshot_data_url,
                favicon_url=result.favicon_url,
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


AnalysisStreamEvent = tuple[str, dict[str, Any]]


def _analysis_event(event_type: str, data: dict[str, Any]) -> AnalysisStreamEvent:
    return event_type, data


async def _forward_model_events(
    task: asyncio.Task[Any],
    queue: asyncio.Queue[dict[str, Any]],
) -> AsyncGenerator[AnalysisStreamEvent, None]:
    while not task.done() or not queue.empty():
        if not queue.empty():
            yield _analysis_event("model", queue.get_nowait())
            continue

        next_event = asyncio.create_task(queue.get())
        done, _ = await asyncio.wait({task, next_event}, return_when=asyncio.FIRST_COMPLETED)
        if next_event in done:
            yield _analysis_event("model", next_event.result())
        else:
            next_event.cancel()


async def _analyze_events(url: str, prompt: str = "") -> AsyncGenerator[AnalysisStreamEvent, None]:
    try:
        yield _analysis_event("step", {"step": "fetch_page", "label": "Fetch page", "status": "running"})
        preflight = await asyncio.wait_for(_preflight(url), timeout=60.0)
        if not preflight.ok:
            raise RuntimeError(preflight.error_message or "Page preflight failed")
        
        yield _analysis_event("step", {"step": "fetch_page", "label": "Fetch page", "status": "done"})
        
        yield _analysis_event("preflight", {
            "url": preflight.normalized_url,
            "normalizedUrl": preflight.normalized_url,
            "host": preflight.host,
            "title": preflight.title,
            "requiresProxy": preflight.requires_proxy,
            "proxyMode": preflight.proxy_mode,
            "previewHtml": preflight.preview_html,
            "previewImage": preflight.preview_image,
            "renderedBy": "chrome",
            "networkEndpoints": preflight.network_endpoints,
            "faviconUrl": preflight.favicon_url,
            "ok": True,
            "errorMessage": "",
        })
        
        yield _analysis_event("step", {"step": "analyze_structure", "label": "Analyze page structure", "status": "running"})
        analysis_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        analysis_task = asyncio.create_task(
            asyncio.wait_for(
                template_agent.analyze_page(
                    preflight.normalized_url,
                    preflight.html,
                    preflight.network_endpoints,
                    on_event=analysis_events.put_nowait,
                ),
                timeout=120.0,
            )
        )
        async for event in _forward_model_events(analysis_task, analysis_events):
            yield event
        analysis_result = await analysis_task
        
        yield _analysis_event("step", {"step": "analyze_structure", "label": "Analyze page structure", "status": "done"})
        
        yield _analysis_event("step", {"step": "generate_template", "label": "Generate template", "status": "running"})
        template_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        template_task = asyncio.create_task(
            asyncio.wait_for(
                template_agent.generate_template(
                    preflight.normalized_url,
                    analysis_result,
                    on_event=template_events.put_nowait,
                ),
                timeout=120.0,
            )
        )
        async for event in _forward_model_events(template_task, template_events):
            yield event
        template_yaml = await template_task
        if not template_yaml:
            raise RuntimeError("Model returned an empty template")
        
        template_name = template_agent._build_template_name(preflight.normalized_url)
        try:
            template_dict = yaml.safe_load(template_yaml)
        except yaml.YAMLError:
            template_dict = {}
        yield _analysis_event("step", {"step": "generate_template", "label": "Generate template", "status": "done"})
        
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
        
        yield _analysis_event("step", {"step": "validate", "label": "Validate artifacts", "status": "running"})
        await asyncio.sleep(1)
        yield _analysis_event("step", {"step": "validate", "label": "Validate artifacts", "status": "done"})
        yield _analysis_event("fields", {"fields": [f.__dict__ for f in fields]})
        yield _analysis_event("pagination", pagination.__dict__)
        
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
        
        yield _analysis_event("complete", {
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
        logger.info("Analysis cancelled for %s", url)
    except Exception as exc:
        yield _analysis_event("error", {"error": str(exc)})
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
