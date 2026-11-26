from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

import yaml
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.config.ai_settings import ai_settings
from app.web.agents.adapter import adapter_agent
from app.web.api.dependencies.common import get_current_user
from app.web.agents.template import template_agent
from app.web.api.models.tasks import TaskAction, WorkspaceTaskRequest
from app.web.services.ai_collect_store import ai_collect_store
from app.web.services.browser_renderer import BrowserRenderResult, browser_renderer
from app.web.services.google_search import GoogleSearchRequest, create_google_search_task
from app.web.services.search import SearchRequest, create_search_task
from app.web.services.task_runs import delete_run, run_action
from app.web.utils.validation import scope_limit, validate_generated_adapter, validate_target_url

router = APIRouter(prefix="/ai", tags=["ai-collect"])


class UrlRequest(BaseModel):
    url: str = Field(min_length=1, max_length=4096)

    @field_validator("url")
    @classmethod
    def check_public_url(cls, value: str) -> str:
        validate_target_url(value)
        return value


class GenerateOptions(BaseModel):
    userRequest: str = Field(default="", max_length=4000)
    currentTemplateYaml: str = Field(default="", max_length=65536)
    viewportWidth: int = Field(default=1440, gt=0)
    viewportHeight: int = Field(default=1000, gt=0)


class GenerateRequest(UrlRequest):
    options: GenerateOptions = Field(default_factory=GenerateOptions)


class DryRunRequest(BaseModel):
    templateId: str
    limit: int = Field(default=20, ge=1, le=1000)


class AdapterRequest(UrlRequest):
    siteType: str = "default"
    templateId: str | None = Field(default=None, max_length=256)
    prompt: str = Field(default="", max_length=4000)
    currentAdapterCode: str = Field(default="", max_length=65536)


class WorkspaceTemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")
    yaml_content: str
    adapter: str = ""
    adapter_code: str = ""
    description: str = ""


class ReleaseRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    analysisId: str | None = None
    name: str
    version: str = "v1.0"
    title: str
    domain: str = ""
    favicon_url: str = ""
    status: Literal["active", "draft", "deprecated"] = "draft"
    yaml_content: str
    adapter: str = ""
    adapter_code: str = ""
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    task: WorkspaceTaskRequest | None = None


def _analysis_response(url: str, analysis: dict[str, Any], yaml_content: str, template_id: str) -> dict[str, Any]:
    fields = analysis.get("fields") or []
    pagination = analysis.get("pagination") or {}
    return {
        "templateId": template_id,
        "name": template_id,
        "domain": analysis.get("domain") or "",
        "yaml": yaml_content,
        "adapter": "",
        "adapterPath": "",
        "fields": fields,
        "pagination": pagination,
        "sampleItems": analysis.get("sample_items") or [],
        "warnings": analysis.get("warnings") or [],
        "acquisition": analysis.get("acquisition") or {},
        "agent": {
            "source": "template_agent", "model": ai_settings.scout_model,
            "decision": {"requires_adapter": bool(analysis.get("adapter_requirements"))},
        },
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }


def _preflight_response(url: str, result: BrowserRenderResult) -> dict[str, Any]:
    return {
        "ok": True,
        "url": url,
        "normalizedUrl": result.url,
        "host": result.url.split("/", 3)[2] if "://" in result.url else "",
        "title": result.title,
        "requiresProxy": False,
        "proxyMode": "direct",
        "previewUrl": result.url,
        "previewImage": result.preview_image,
        "renderedBy": "chrome",
        "networkEndpoints": result.json_endpoints,
        "networkResponses": result.network_responses,
        "browserEvents": result.browser_events,
        "pageWarnings": [],
        "faviconUrl": result.favicon_url,
        "errorCode": "",
        "errorMessage": "",
    }


@router.post("/preflight", dependencies=[Depends(get_current_user)])
async def preflight(payload: UrlRequest) -> dict[str, Any]:
    return _preflight_response(payload.url, await browser_renderer.render(payload.url))


async def build_template(payload: GenerateRequest, emit: Callable[[str, dict], None] | None = None) -> dict[str, Any]:
    options = payload.options
    if emit:
        emit("analyze_step", {"step": "fetch_page", "label": "正在渲染采集页面", "status": "running"})
    rendered = await browser_renderer.render(
        payload.url, viewport_width=options.viewportWidth, viewport_height=options.viewportHeight,
    )
    validate_target_url(rendered.url)
    if emit:
        emit("analyze_preflight", _preflight_response(payload.url, rendered))
        emit("analyze_step", {"step": "fetch_page", "label": "页面证据已就绪", "status": "done"})
    evidence = await template_agent.analyze_page(
        rendered.url,
        rendered.html,
        rendered.json_endpoints,
        rendered.network_responses,
        [],
    )
    yaml_content = await template_agent.generate_template(
        rendered.url,
        evidence,
        rendered.title,
        user_request=options.userRequest,
        existing_template_yaml=options.currentTemplateYaml,
        on_event=(lambda event: emit("analyze_model", event)) if emit else None,
    )
    if len(yaml_content.encode()) > scope_limit("max_generated_template_bytes", 65536):
        raise HTTPException(422, "Generated template exceeds the collection size limit")
    template = yaml.safe_load(yaml_content)
    evidence = template_agent.merge_template_evidence(evidence, template)
    if len(evidence["fields"]) > scope_limit("max_generated_fields", 100):
        raise HTTPException(422, "Generated template exceeds the collection field limit")
    if template.get("list_pagination") and not 0 < int(template["list_pagination"].get("max_pages", 1000)) <= scope_limit("max_template_pages", 100):
        raise HTTPException(422, "Generated template exceeds the collection page limit")
    template_id = template_agent._build_template_name(rendered.url)
    await ai_collect_store.save_analysis({
        "template_id": template_id,
        "source_url": rendered.url,
        "template_name": template_id,
        "template_yaml": yaml_content,
        "adapter_code": "",
        "fields": evidence.get("fields") or [],
        "pagination": evidence.get("pagination") or {},
        "sample_items": evidence.get("sample_items") or [],
    })
    return _analysis_response(rendered.url, evidence, yaml_content, template_id)


@router.post("/generate-template", dependencies=[Depends(get_current_user)])
async def generate_template(payload: GenerateRequest) -> dict[str, Any]:
    return await build_template(payload)


@router.post("/dry-run")
async def dry_run(payload: DryRunRequest) -> dict[str, Any]:
    analysis = await ai_collect_store.get_analysis(payload.templateId)
    if analysis is None:
        raise HTTPException(status_code=404, detail="分析结果不存在")
    sample_items = analysis.get("sample_items") or []
    return {
        "totalPages": 1,
        "totalItems": min(len(sample_items), payload.limit),
        "sampleItems": sample_items[:payload.limit],
        "columns": list(sample_items[0].keys()) if sample_items and isinstance(sample_items[0], dict) else [],
        "duration": 0,
        "errors": [],
    }


async def build_adapter(payload: AdapterRequest, on_chunk: Callable[[str], None] | None = None) -> dict[str, Any]:
    template_id = payload.templateId or template_agent._build_template_name(payload.url)
    analysis = await ai_collect_store.get_analysis(template_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="模板分析结果不存在")
    result = await adapter_agent.generate_adapter(
        template_id, str(analysis["template_yaml"]), on_chunk=on_chunk,
        user_request=payload.prompt, existing_adapter_code=payload.currentAdapterCode,
    )
    if len(result.adapter_code.encode()) > scope_limit("max_generated_adapter_bytes", 65536):
        raise HTTPException(422, "Generated adapter exceeds the collection size limit")
    validate_generated_adapter(result.adapter_code)
    if result.warnings:
        raise HTTPException(422, "；".join(result.warnings))
    await ai_collect_store.save_analysis({
        "template_id": template_id,
        "source_url": str(analysis.get("source_url") or payload.url),
        "template_name": str(analysis.get("template_name") or template_id),
        "template_yaml": str(analysis.get("template_yaml") or ""),
        "adapter_code": result.adapter_code,
        "fields": analysis.get("fields") or [],
        "pagination": analysis.get("pagination") or {},
        "sample_items": analysis.get("sample_items") or [],
    })
    return {
        "adapterId": result.adapter_name or template_id,
        "code": result.adapter_code,
        "language": "python",
        "testResult": {"passed": not result.warnings, "sampleCount": 0},
    }


@router.post("/generate-adapter", dependencies=[Depends(get_current_user)])
async def generate_adapter(payload: AdapterRequest) -> dict[str, Any]:
    return await build_adapter(payload)


@router.get("/workspace/templates")
async def workspace_templates(include_definitions: bool = True) -> dict[str, Any]:
    if not include_definitions:
        return {"items": await ai_collect_store.list_templates()}
    return {"items": await ai_collect_store.list_template_definitions(include_adapter_code=True)}


@router.put("/workspace/templates/{template_id}")
async def update_workspace_template(template_id: str, payload: WorkspaceTemplateUpdate) -> dict[str, Any]:
    item = await ai_collect_store.update_template(template_id, payload.model_dump())
    if item is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return item


@router.post("/workspace/templates/release")
async def release_workspace_template(payload: ReleaseRequest) -> dict[str, Any]:
    try:
        template = await ai_collect_store.release_template(payload.model_dump(exclude={"task"}))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    task = None
    if payload.task is not None:
        task = await ai_collect_store.create_task(payload.task.model_dump())
    return {"template": template, "task": task, "artifacts": {}}


@router.get("/workspace/tasks")
async def workspace_tasks() -> dict[str, Any]:
    return {"items": await ai_collect_store.list_tasks()}


@router.get("/workspace/tasks/{task_id}")
async def workspace_task(task_id: str) -> dict[str, Any]:
    task = await ai_collect_store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/workspace/tasks/{task_id}/logs/{run_id}")
async def workspace_task_logs(
    task_id: UUID,
    run_id: UUID,
    before_id: int | None = Query(default=None, ge=1, le=9223372036854775807),
    limit: int = Query(default=200, ge=1, le=500),
) -> dict[str, Any]:
    logs = await ai_collect_store.get_task_logs(
        str(task_id), str(run_id), before_id=before_id, limit=limit + 1, include_notifications=False,
    )
    notifications = await ai_collect_store.get_task_notifications(str(task_id), str(run_id)) if before_id is None else []
    has_more = len(logs) > limit
    items = logs[-limit:]
    return {
        "items": items,
        "notifications": notifications,
        "has_more": has_more,
        "next_before_id": str(items[0]["id"]) if has_more else None,
    }


@router.get("/workspace/tasks/{task_id}/log-runs")
async def workspace_task_log_runs(task_id: str, offset: int = 0, limit: int = 20) -> dict[str, Any]:
    return {"items": await ai_collect_store.get_task_log_runs(task_id, offset, min(limit, 100))}


@router.get("/workspace/tasks/{task_id}/log-runs/{run_id}")
async def workspace_task_log_run(task_id: UUID, run_id: UUID) -> dict[str, Any]:
    run = await ai_collect_store.get_task_run(str(task_id), str(run_id))
    if run is None:
        raise HTTPException(status_code=404, detail="执行批次不存在")
    return run


@router.post("/workspace/tasks")
async def create_workspace_task(payload: WorkspaceTaskRequest) -> dict[str, Any]:
    return await ai_collect_store.create_task(payload.model_dump())


@router.post("/google-search/tasks", status_code=201)
async def create_google_search(payload: GoogleSearchRequest) -> dict[str, Any]:
    return await create_google_search_task(payload)


@router.post("/search/tasks", status_code=201)
async def create_search(payload: SearchRequest) -> dict[str, Any]:
    return await create_search_task(payload)


@router.post("/workspace/batch-inputs")
async def upload_workspace_batch_input(
    file: UploadFile = File(...),
    template_name: str = Form(...),
    template_version: str = Form("v1.0"),
) -> dict[str, Any]:
    content = await file.read()
    object_key = await ai_collect_store.upload_batch_input(
        template_name,
        template_version,
        file.filename or "input",
        __import__("io").BytesIO(content),
        len(content),
        file.content_type or "application/octet-stream",
    )
    return {"object_key": object_key, "filename": file.filename or "input", "size": len(content)}


@router.get("/platform/overview")
async def platform_overview() -> dict[str, Any]:
    templates = await ai_collect_store.list_templates()
    tasks = await ai_collect_store.list_tasks()
    return {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "healthScore": 100,
            "templateCount": len(templates),
            "sourceCount": len(templates),
            "liveTaskCount": sum(1 for task in tasks if task.get("status") == "running"),
            "dataDomainCount": len({str(item.get("domain") or "") for item in templates}),
            "healthyStageCount": 1,
        },
        "stages": [], "sources": [], "taskBoard": [], "etlLayers": [],
        "guardrails": [], "recommendations": [],
    }


@router.post("/workspace/tasks/{task_id}/action")
async def workspace_task_action(task_id: UUID, payload: TaskAction) -> dict[str, Any]:
    return await run_action(str(task_id), payload.action)


@router.delete("/workspace/tasks/{task_id}")
async def delete_workspace_task(task_id: UUID) -> dict[str, bool]:
    return await delete_run(str(task_id))
