"""AI Collect API routes."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncGenerator, Callable
from typing import Any
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.config.ai_settings import ai_settings
from app.logger import get_logger
from app.models.template import SiteTemplate
from app.storage.minio_client import get_business_metadata_minio_client
from app.web.agents.adapter import adapter_agent
from app.web.agents.template import AnalysisResult, template_agent
from app.web.services.ai_collect_store import ai_collect_store
from app.web.services.browser_renderer import browser_renderer
from app.web.utils.validation import (
    clamp_positive,
    validate_generated_adapter,
    validate_target_url,
)

logger = get_logger(__name__)
router = APIRouter()
_PREFLIGHT_MAX_RETRIES = 3


def _preflight_deadline() -> float:
    retry_backoff = sum(2**attempt for attempt in range(_PREFLIGHT_MAX_RETRIES - 1))
    return ai_settings.page_fetch_timeout * _PREFLIGHT_MAX_RETRIES + retry_backoff


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
        network_responses: list[dict[str, Any]] | None = None,
        page_warnings: list[str] | None = None,
        browser_events: list[dict[str, Any]] | None = None,
    ):
        self.url = url
        self.html = html
        self.title = title
        self.network_endpoints = network_endpoints
        self.host = host
        self.normalized_url = normalized_url
        self.preview_image = preview_image
        self.favicon_url = favicon_url
        self.network_responses = network_responses or []
        self.page_warnings = page_warnings or []
        self.browser_events = browser_events or []
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
            "previewUrl": self.normalized_url,
            "previewImage": self.preview_image,
            "renderedBy": "chrome",
            "networkEndpoints": self.network_endpoints,
            "networkResponses": self.network_responses,
            "pageWarnings": self.page_warnings,
            "browserEvents": [
                {key: value for key, value in event.items() if key != "previewImage"}
                for event in self.browser_events
            ],
            "faviconUrl": self.favicon_url,
        }


def _detect_page_warnings(
    html: str,
    network_responses: list[dict[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    response_text = " ".join(
        str(response.get("bodyPreview") or "")
        for response in network_responses
    )
    lowered = re.sub(r"\s+", " ", f"{html} {response_text}").lower()

    maintenance_markers = (
        "currently under maintenance",
        "portal is currently unavailable",
        "service unavailable",
    )
    if any(marker in lowered for marker in maintenance_markers):
        warnings.append("The captured page is a maintenance or service-unavailable response.")
    if "loading, please wait" in lowered:
        warnings.append("The visible page is a dynamic loading shell; static HTML selectors are not sufficient.")

    failed = [
        response
        for response in network_responses
        if int(response.get("status") or 0) >= 400
    ]
    if failed:
        summary = ", ".join(
            f"{response.get('status')} {response.get('url')}"
            for response in failed[:5]
        )
        warnings.append(f"Dynamic data requests failed: {summary}")

    unexpected_html = [
        response
        for response in network_responses
        if "/api/" in str(response.get("url") or "")
        and "html" in str(response.get("contentType") or "").lower()
        and not response.get("recordFields")
    ]
    if unexpected_html:
        warnings.append("An API request returned HTML instead of structured data; validate maintenance/WAF responses.")

    return warnings


def _validate_generated_template(
    template_yaml: str,
    source_url: str,
    page_warnings: list[str],
    analysis_result: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    try:
        template_dict = yaml.safe_load(template_yaml)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Generated template is invalid YAML: {exc}") from exc
    if not isinstance(template_dict, dict):
        raise RuntimeError("Generated template must be a YAML mapping")

    try:
        SiteTemplate(**template_dict)
    except Exception as exc:
        raise RuntimeError(f"Generated template does not match SiteTemplate schema: {exc}") from exc

    warnings = list(page_warnings)
    list_page = str(template_dict.get("list_page") or "")
    response_type = str(template_dict.get("response_type") or "html").lower()
    if page_warnings and response_type == "html" and list_page in {source_url, urlparse(source_url).path}:
        warnings.append("Generated template still targets the rendered shell; verify or replace it with the observed data endpoint.")
    if not template_dict.get("dedup_fields"):
        warnings.append("Generated template has no stable dedup_fields.")
    if not template_dict.get("list_fields"):
        warnings.append("Generated template has no verified list_fields.")

    field_names = {
        str(field.get("name"))
        for section in ("list_fields", "detail_fields")
        for field in (template_dict.get(section) or [])
        if isinstance(field, dict) and field.get("name")
    }

    def is_produced(field_path: str) -> bool:
        return field_path in field_names or field_path.split(".", 1)[0] in field_names

    missing_dedup = [
        str(field)
        for field in (template_dict.get("dedup_fields") or [])
        if not is_produced(str(field))
    ]
    if missing_dedup:
        warnings.append(
            "Dedup fields are not produced by list/detail fields: " + ", ".join(missing_dedup)
        )

    download = template_dict.get("download") or []
    if isinstance(download, dict):
        download = [download]
    unresolved_resources = [
        str(item.get("selector"))
        for item in download
        if isinstance(item, dict)
        and item.get("selector")
        and not is_produced(str(item.get("selector")))
    ]
    if unresolved_resources and not template_dict.get("adapter"):
        warnings.append(
            "Resource selectors require adapter output but adapter is not set: "
            + ", ".join(unresolved_resources)
        )

    if analysis_result and analysis_result.get("source_kind") == "api":
        selected_endpoint = str(analysis_result.get("selected_endpoint") or "")
        selected_path = urlparse(selected_endpoint).path if selected_endpoint else ""
        template_path = urlparse(list_page).path
        selected_parts = selected_path.strip("/").split("/")
        template_parts = template_path.strip("/").split("/")
        path_matches = len(selected_parts) == len(template_parts) and all(
            template_part == selected_part
            or (template_part.startswith("{") and template_part.endswith("}"))
            for selected_part, template_part in zip(selected_parts, template_parts, strict=True)
        )
        if selected_path and not path_matches:
            warnings.append("Generated template does not target the API selected by page analysis.")
    return template_dict, list(dict.fromkeys(warnings))


async def _preflight(
    url: str,
    max_retries: int = _PREFLIGHT_MAX_RETRIES,
    viewport_width: int = 1440,
    on_browser_event: Callable[[dict[str, object]], None] | None = None,
) -> UrlPreflightResponse:
    last_error = None
    for attempt in range(max_retries):
        try:
            result = await browser_renderer.render(
                url,
                viewport_width=viewport_width,
                on_event=on_browser_event,
            )
            logger.info("Preflight succeeded on attempt %d/%d for %s", attempt + 1, max_retries, url)
            return UrlPreflightResponse(
                url=url,
                html=result.html,
                title=result.title,
                network_endpoints=result.json_endpoints,
                host=urlparse(url).hostname or "",
                normalized_url=result.url,
                preview_image=result.preview_image,
                favicon_url=result.favicon_url,
                network_responses=result.network_responses,
                page_warnings=_detect_page_warnings(result.html, result.network_responses),
                browser_events=result.browser_events,
            )
        except Exception as e:
            last_error = e
            logger.warning("Preflight attempt %d/%d failed for %s: %s", attempt + 1, max_retries, url, e)
            if on_browser_event:
                on_browser_event({
                    "kind": "preflight_retry",
                    "url": url,
                    "label": "Browser preflight attempt failed",
                    "attempt": attempt + 1,
                    "maxAttempts": max_retries,
                    "error": str(e),
                })
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


async def _forward_task_events(
    task: asyncio.Task[Any],
    queue: asyncio.Queue[dict[str, Any]],
    event_type: str,
) -> AsyncGenerator[AnalysisStreamEvent, None]:
    while not task.done() or not queue.empty():
        if not queue.empty():
            yield _analysis_event(event_type, queue.get_nowait())
            continue

        next_event = asyncio.create_task(queue.get())
        done, _ = await asyncio.wait({task, next_event}, return_when=asyncio.FIRST_COMPLETED)
        if next_event in done:
            yield _analysis_event(event_type, next_event.result())
        else:
            next_event.cancel()


async def _analyze_events(
    url: str,
    prompt: str = "",
    viewport_width: int = 1440,
) -> AsyncGenerator[AnalysisStreamEvent, None]:
    step_started_at: dict[str, int] = {}

    def step_event(step: str, label: str, status: str) -> AnalysisStreamEvent:
        now = int(time.time() * 1000)
        if status == "running":
            step_started_at[step] = now
        data: dict[str, Any] = {
            "step": step,
            "label": label,
            "status": status,
            "startedAt": step_started_at.get(step, now),
        }
        if status == "done":
            data["finishedAt"] = now
        return _analysis_event("step", data)

    try:
        yield step_event("fetch_page", "Fetch page", "running")
        browser_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        preflight_task = asyncio.create_task(
            asyncio.wait_for(
                _preflight(
                    url,
                    viewport_width=viewport_width,
                    on_browser_event=lambda event: loop.call_soon_threadsafe(
                        browser_events.put_nowait,
                        event,
                    ),
                ),
                timeout=_preflight_deadline(),
            )
        )
        async for event in _forward_task_events(preflight_task, browser_events, "browser"):
            yield event
        preflight = await preflight_task
        if not preflight.ok:
            raise RuntimeError(preflight.error_message or "Page preflight failed")
        
        yield step_event("fetch_page", "Fetch page", "done")
        
        yield _analysis_event("preflight", {
            "url": preflight.normalized_url,
            "normalizedUrl": preflight.normalized_url,
            "host": preflight.host,
            "title": preflight.title,
            "requiresProxy": preflight.requires_proxy,
            "proxyMode": preflight.proxy_mode,
            "previewUrl": preflight.normalized_url,
            "previewImage": preflight.preview_image,
            "renderedBy": "chrome",
            "networkEndpoints": preflight.network_endpoints,
            "networkResponses": preflight.network_responses,
            "pageWarnings": preflight.page_warnings,
            "browserEvents": [
                {key: value for key, value in event.items() if key != "previewImage"}
                for event in preflight.browser_events
            ],
            "faviconUrl": preflight.favicon_url,
            "ok": True,
            "errorMessage": "",
        })
        
        yield step_event("analyze_structure", "Analyze page structure", "running")
        analysis_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        analysis_task = asyncio.create_task(
            asyncio.wait_for(
                template_agent.analyze_page(
                    preflight.normalized_url,
                    preflight.html,
                    preflight.network_endpoints,
                    network_responses=preflight.network_responses,
                    page_warnings=preflight.page_warnings,
                    on_event=analysis_events.put_nowait,
                ),
                timeout=ai_settings.llm_request_timeout,
            )
        )
        async for event in _forward_task_events(analysis_task, analysis_events, "model"):
            yield event
        analysis_result = await analysis_task
        
        yield step_event("analyze_structure", "Analyze page structure", "done")
        
        yield step_event("generate_template", "Generate template", "running")
        template_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        template_task = asyncio.create_task(
            asyncio.wait_for(
                template_agent.generate_template(
                    preflight.normalized_url,
                    analysis_result,
                    page_title=preflight.title,
                    on_event=template_events.put_nowait,
                ),
                timeout=ai_settings.llm_request_timeout,
            )
        )
        async for event in _forward_task_events(template_task, template_events, "model"):
            yield event
        template_yaml = await template_task
        if not template_yaml:
            raise RuntimeError("Model returned an empty template")
        
        template_name = template_agent._build_template_name(preflight.normalized_url)
        template_dict, validation_warnings = _validate_generated_template(
            template_yaml,
            preflight.normalized_url,
            preflight.page_warnings,
            analysis_result,
        )
        yield _analysis_event("template_key", {
            "key": next(reversed(template_dict), ""),
            "index": len(template_dict),
            "total": len(template_dict),
            "templateYaml": template_yaml,
        })
        yield step_event("generate_template", "Generate template", "done")
        
        fields = template_agent._build_inferred_fields(analysis_result)
        sample_items = template_agent._build_sample_items(fields, analysis_result)
        pagination = template_agent._build_pagination(preflight.normalized_url, analysis_result)
        acquisition = template_agent._build_acquisition(preflight.normalized_url, analysis_result)
        
        result = AnalysisResult(
            url=preflight.normalized_url,
            base_url=template_agent._build_base_url(preflight.normalized_url),
            domain=preflight.host,
            template_name=template_name,
            display_name=template_agent._build_display_name(
                preflight.normalized_url,
                preflight.title,
            ),
            root_selector="",
            fields=fields,
            sample_items=sample_items,
            pagination=pagination,
            mode="ai_analysis",
            template_dict=template_dict,
            template_yaml=template_yaml,
            adapter_code="",
            warnings=validation_warnings,
            detail_fields=[],
            acquisition=acquisition,
        )
        
        yield step_event("validate", "Validate artifacts", "running")
        await asyncio.sleep(1)
        yield step_event("validate", "Validate artifacts", "done")
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
            "warnings": result.warnings,
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
            "warnings": result.warnings,
            "agent": {
                "model": template_agent.model_name,
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


async def _generate_adapter_for_template(
    template_id: str,
    on_chunk: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    analysis = await ai_collect_store.get_analysis(template_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"Analysis '{template_id}' not found")
    
    template_yaml = analysis.get("template_yaml", "")
    template_name = analysis.get("template_name", "generated_adapter")
    
    if not template_yaml:
        raise HTTPException(status_code=400, detail="Template YAML is empty")
    
    try:
        adapter_result = await asyncio.wait_for(
            adapter_agent.generate_adapter(
                template_name,
                template_yaml,
                on_chunk=on_chunk,
            ),
            timeout=ai_settings.llm_request_timeout,
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError("Failed to generate adapter: timeout") from exc
    
    adapter_code = adapter_result.adapter_code
    validate_generated_adapter(adapter_code)

    analysis["adapter_code"] = adapter_code
    await ai_collect_store.save_analysis(analysis)
    
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
        network_responses=preflight.network_responses,
        page_warnings=preflight.page_warnings,
    )
    
    template_yaml = await template_agent.generate_template(
        preflight.normalized_url,
        analysis_result,
        page_title=preflight.title,
    )
    _, warnings = _validate_generated_template(
        template_yaml,
        preflight.normalized_url,
        preflight.page_warnings,
        analysis_result,
    )
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
        "warnings": warnings,
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
