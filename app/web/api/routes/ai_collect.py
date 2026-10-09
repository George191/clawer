from __future__ import annotations

from uuid import UUID
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, ConfigDict, Field

from app.web.agents.adapter import AdapterAgent
from app.web.agents.template import template_agent
from app.web.services.ai_collect_store import ai_collect_store
from app.web.services.browser_renderer import browser_renderer
from app.web.api.models.tasks import TaskAction, WorkspaceTaskRequest
from app.web.services.task_runs import delete_run, run_action

router = APIRouter(prefix="/ai", tags=["ai-collect"])


class UrlRequest(BaseModel):
    url: str = Field(min_length=1)


class GenerateRequest(UrlRequest):
    options: dict[str, Any] = Field(default_factory=dict)


class DryRunRequest(BaseModel):
    templateId: str
    limit: int = Field(default=20, ge=1, le=1000)


class AdapterRequest(UrlRequest):
    siteType: str = "default"
    templateId: str | None = None


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
        "agent": {"source": "template_agent"},
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/preflight")
async def preflight(payload: UrlRequest) -> dict[str, Any]:
    result = await browser_renderer.render(payload.url)
    return {
        "ok": True,
        "url": payload.url,
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


@router.post("/generate-template")
async def generate_template(payload: GenerateRequest) -> dict[str, Any]:
    rendered = await browser_renderer.render(payload.url)
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
        user_request=str(payload.options.get("userRequest") or ""),
    )
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


@router.post("/generate-adapter")
async def generate_adapter(payload: AdapterRequest) -> dict[str, Any]:
    template_id = payload.templateId or template_agent._build_template_name(payload.url)
    analysis = await ai_collect_store.get_analysis(template_id)
    if analysis is None:
        raise HTTPException(status_code=404, detail="模板分析结果不存在")
    result = await AdapterAgent().generate_adapter(template_id, str(analysis["template_yaml"]))
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
        "adapterId": result.adapter_name or f"{template_id}_adapter",
        "code": result.adapter_code,
        "language": "python",
        "testResult": {"passed": not result.warnings, "sampleCount": 0},
    }


@router.get("/workspace/templates")
async def workspace_templates() -> dict[str, Any]:
    return {"items": await ai_collect_store.list_template_definitions(include_adapter_code=True)}


@router.put("/workspace/templates/{template_id}")
async def update_workspace_template(template_id: str, payload: WorkspaceTemplateUpdate) -> dict[str, Any]:
    item = await ai_collect_store.update_template(template_id, payload.model_dump())
    if item is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    return item


@router.post("/workspace/templates/release")
async def release_workspace_template(payload: ReleaseRequest) -> dict[str, Any]:
    template = await ai_collect_store.release_template(payload.model_dump(exclude={"task"}))
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
async def workspace_task_logs(task_id: str, run_id: str) -> dict[str, Any]:
    return {"items": await ai_collect_store.get_task_logs(task_id, run_id)}


@router.get("/workspace/tasks/{task_id}/log-runs")
async def workspace_task_log_runs(task_id: str, offset: int = 0, limit: int = 20) -> dict[str, Any]:
    return {"items": await ai_collect_store.get_task_log_runs(task_id, offset, min(limit, 100))}


@router.post("/workspace/tasks")
async def create_workspace_task(payload: WorkspaceTaskRequest) -> dict[str, Any]:
    return await ai_collect_store.create_task(payload.model_dump())


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
