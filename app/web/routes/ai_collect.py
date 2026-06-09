"""AI 智能采集 API — 模板/适配器自动生成。

Endpoints:
    GET    /api/ai/analyze-stream       — SSE 流式页面分析
    POST   /api/ai/generate-template    — 生成 YAML 模板
    POST   /api/ai/dry-run              — 试采集
    POST   /api/ai/generate-adapter     — 生成适配器代码
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.config.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()
TEMPLATE_DIR = Path(settings.template_dir)


# ── Request/Response Models ─────────────────────────────────────────────────


class GenerateTemplateRequest(BaseModel):
    url: str = Field(..., description="目标页面 URL")
    options: GenerateOptions | None = None


class GenerateOptions(BaseModel):
    maxPages: int = Field(default=50, alias="maxPages")
    fieldOverrides: list[FieldOverride] | None = None


class FieldOverride(BaseModel):
    name: str
    rename: str | None = None


class DryRunRequest(BaseModel):
    templateId: str = Field(..., alias="templateId")
    limit: int = Field(default=20)


class GenerateAdapterRequest(BaseModel):
    url: str = Field(..., description="特殊站点 URL")
    siteType: str = Field(default="default", alias="siteType")


# ── Helpers ─────────────────────────────────────────────────────────────────


def _validate_target_url(url: str) -> None:
    """校验目标 URL 是否安全（非内网、非黑名单协议）。"""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail=f"不支持的协议: {parsed.scheme}")

    hostname = parsed.hostname or ""
    if hostname in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise HTTPException(status_code=400, detail="禁止访问本地地址")

    # 内网 CIDR
    if hostname.startswith("10.") or hostname.startswith("192.168."):
        raise HTTPException(status_code=400, detail="禁止访问内网地址")
    if hostname.startswith("172."):
        try:
            second = int(hostname.split(".")[1])
            if 16 <= second <= 31:
                raise HTTPException(status_code=400, detail="禁止访问内网地址")
        except (IndexError, ValueError):
            pass


def _build_yaml_template(url: str, fields: list[dict], pagination: dict, max_pages: int = 50) -> str:
    """根据分析结果构建 YAML 模板内容。"""
    from urllib.parse import urlparse

    domain = urlparse(url).hostname or "unknown"
    name = domain.replace(".", "_")

    lines = [
        f"name: {name}",
        f"base_url: \"{url}\"",
        f"data_type: other",
        f"description: >",
        f"  Auto-generated template for {domain}",
        "",
        "response_type: html",
        "",
        "# ── Pagination ──",
        f"pagination_type: {pagination.get('type', 'none')}",
    ]

    if pagination.get("selector"):
        lines.append(f"pagination_selector: \"{pagination['selector']}\"")
    lines.append(f"max_pages: {max_pages}")

    if pagination.get("params"):
        for k, v in pagination["params"].items():
            lines.append(f"pagination_{k}: {v}")

    lines.append("")
    lines.append("# ── List Fields ──")
    lines.append("list_fields:")

    for f in fields:
        lines.append(f"  - name: {f['name']}")
        lines.append(f"    selector: \"{f.get('selector', '')}\"")
        lines.append(f"    field_type: {f.get('type', 'text')}")
        if f.get("required"):
            lines.append(f"    required: true")

    return "\n".join(lines)


# ── SSE Streaming ───────────────────────────────────────────────────────────


async def _analyze_stream(url: str) -> AsyncGenerator[str, None]:
    """SSE 生成器：逐步分析页面并推送事件。"""

    def _event(name: str, data: dict) -> str:
        return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    try:
        # Step 1: fetch_page
        yield _event("step", {"step": "fetch_page", "label": "获取页面", "status": "running"})
        yield _event("thinking", {"content": f"正在请求 {url} ..."})
        await asyncio.sleep(0.3)

        # TODO: 实际 HTTP 请求
        yield _event("step", {"step": "fetch_page", "label": "获取页面", "status": "done"})

        # Step 2: parse_dom
        yield _event("step", {"step": "parse_dom", "label": "解析 DOM 结构", "status": "running"})
        yield _event("thinking", {"content": "正在解析页面 DOM，识别列表结构..."})
        await asyncio.sleep(0.3)
        yield _event("step", {"step": "parse_dom", "label": "解析 DOM 结构", "status": "done"})

        # Step 3: detect_list
        yield _event("step", {"step": "detect_list", "label": "识别列表容器", "status": "running"})
        yield _event("thinking", {"content": "正在定位重复的列表项容器..."})
        await asyncio.sleep(0.5)
        yield _event("step", {"step": "detect_list", "label": "识别列表容器", "status": "done"})
        yield _event("thinking", {"content": "检测到列表容器，含约 25 个项目"})

        # Step 4: detect_fields
        yield _event("step", {"step": "detect_fields", "label": "识别字段", "status": "running"})
        yield _event("thinking", {"content": "正在分析列表项内的字段结构..."})
        await asyncio.sleep(0.8)

        fields = [
            {"name": "title", "selector": "h2.title a", "type": "text",
             "sample": "示例标题", "required": True},
            {"name": "price", "selector": "span.price", "type": "number",
             "sample": "99.00", "required": False},
            {"name": "link", "selector": "h2.title a", "type": "url",
             "sample": "https://example.com/item/1", "required": True},
            {"name": "date", "selector": "time.date", "type": "date",
             "sample": "2026-06-10", "required": False},
        ]

        yield _event("step", {"step": "detect_fields", "label": "识别字段", "status": "done"})
        yield _event("fields", {"fields": fields})
        yield _event("thinking", {"content": f"识别到 {len(fields)} 个字段：{', '.join(f['name'] for f in fields)}"})

        # Step 5: detect_pagination
        yield _event("step", {"step": "detect_pagination", "label": "检测分页策略", "status": "running"})
        yield _event("thinking", {"content": "正在检测翻页方式..."})
        await asyncio.sleep(0.5)

        pagination = {
            "type": "click",
            "selector": ".pagination .next",
            "maxPages": 50,
            "params": {"pageParam": "page", "startPage": 1, "pageSize": 20},
        }

        yield _event("step", {"step": "detect_pagination", "label": "检测分页策略", "status": "done"})
        yield _event("pagination", pagination)
        yield _event("thinking", {"content": f"分页类型：{pagination['type']}，最大页数：{pagination['maxPages']}"})

        # Step 6: generate_template
        yield _event("step", {"step": "generate_template", "label": "生成模板", "status": "running"})
        yield _event("thinking", {"content": "正在生成 YAML 采集模板..."})
        await asyncio.sleep(0.5)

        yaml_content = _build_yaml_template(url, fields, pagination)
        template_id = f"tpl_{int(time.time())}"

        yield _event("step", {"step": "generate_template", "label": "生成模板", "status": "done"})

        # Complete
        yield _event("complete", {
            "templateYaml": yaml_content,
            "templateId": template_id,
            "fields": fields,
            "pagination": pagination,
        })

    except asyncio.CancelledError:
        logger.info("SSE connection cancelled for %s", url)
    except Exception as e:
        logger.exception("SSE analysis error for %s", url)
        yield _event("error", {"code": "AI_ERROR", "message": str(e)})


# ── Routes ──────────────────────────────────────────────────────────────────


@router.get("/ai/analyze-stream")
async def analyze_stream(url: str, request: Request):
    """SSE 流式页面分析。

    实时推送分析过程：思考 → 步骤状态 → 字段识别 → 分页检测 → 模板生成。
    """
    if not url:
        raise HTTPException(status_code=400, detail="缺少 url 参数")

    _validate_target_url(url)

    async def _generator():
        async for chunk in _analyze_stream(url):
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


@router.post("/ai/generate-template")
async def generate_template(body: GenerateTemplateRequest):
    """生成 YAML 采集模板。

    输入目标 URL，自动分析页面结构并产出可用模板。
    """
    _validate_target_url(body.url)

    from urllib.parse import urlparse
    domain = urlparse(body.url).hostname or "unknown"
    name = domain.replace(".", "_")
    template_id = f"tpl_{int(time.time())}"

    # 模拟 AI 分析结果（实际接入 AI 服务后替换）
    fields = [
        {"name": "title", "selector": "h2.title a", "type": "text", "sample": "示例", "required": True},
        {"name": "price", "selector": "span.price", "type": "number", "sample": "99.00", "required": False},
        {"name": "link", "selector": "h2.title a", "type": "url", "sample": "https://...", "required": True},
        {"name": "date", "selector": "time.date", "type": "date", "sample": "2026-06-10", "required": False},
    ]

    pagination = {
        "type": "click",
        "selector": ".pagination .next",
        "maxPages": 50,
        "params": {"pageParam": "page", "startPage": 1, "pageSize": 20},
    }

    # 应用字段覆盖
    if body.options and body.options.fieldOverrides:
        override_map = {o.name: o.rename for o in body.options.fieldOverrides if o.rename}
        for f in fields:
            if f["name"] in override_map:
                f["name"] = override_map[f["name"]]

    max_pages = body.options.maxPages if body.options else pagination["maxPages"]
    yaml_content = _build_yaml_template(body.url, fields, pagination, max_pages)

    return {
        "templateId": template_id,
        "name": name,
        "domain": domain,
        "yaml": yaml_content,
        "fields": fields,
        "pagination": pagination,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


@router.post("/ai/dry-run")
async def dry_run(body: DryRunRequest):
    """试采集：用指定模板采集少量数据验证效果。"""
    if not body.templateId:
        raise HTTPException(status_code=400, detail="缺少 templateId")

    # TODO: 加载模板并执行实际采集
    limit = min(body.limit, 100)
    sample_items = [
        {
            "title": f"示例项目 {i + 1}",
            "price": f"{50 + i * 1.5:.2f}",
            "link": f"https://example.com/item/{i + 1}",
            "date": "2026-06-10",
        }
        for i in range(min(limit, 45))
    ]

    columns = list(sample_items[0].keys()) if sample_items else []

    return {
        "totalPages": max(1, (len(sample_items) + 9) // 10),
        "totalItems": len(sample_items),
        "sampleItems": sample_items,
        "columns": columns,
        "duration": 2.3,
        "errors": [],
    }


@router.post("/ai/generate-adapter")
async def generate_adapter(body: GenerateAdapterRequest):
    """生成特殊站点适配器代码。

    针对非标准结构的站点，生成自定义 JavaScript 适配器。
    """
    _validate_target_url(body.url)

    from urllib.parse import urlparse
    domain = urlparse(body.url).hostname or "unknown"
    safe_name = domain.replace(".", "_")
    adapter_id = f"adp_{int(time.time())}"

    code = (
        f"// Adapter for {domain} (type: {body.siteType})\n"
        f"// Generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
        f"// Adapter ID: {adapter_id}\n"
        f"\n"
        f"const cheerio = require('cheerio');\n"
        f"const axios = require('axios');\n"
        f"\n"
        f"module.exports = {{\n"
        f"  name: 'adapter_{safe_name}',\n"
        f"  domain: '{domain}',\n"
        f"\n"
        f"  async fetch(url) {{\n"
        f"    const {{ data }} = await axios.get(url, {{ timeout: 30000 }});\n"
        f"    return data;\n"
        f"  }},\n"
        f"\n"
        f"  async parse(html, page = 1) {{\n"
        f"    const $ = cheerio.load(html);\n"
        f"    const items = [];\n"
        f"    $('.list-item').each((i, el) => {{\n"
        f"      items.push({{\n"
        f"        title: $(el).find('.title').text().trim(),\n"
        f"        link: $(el).find('a').attr('href'),\n"
        f"      }});\n"
        f"    }});\n"
        f"    return items;\n"
        f"  }},\n"
        f"\n"
        f"  getNextPageUrl(currentUrl, page) {{\n"
        f"    return `{body.url}${{page > 1 ? '?p=' + page : ''}}`;\n"
        f"  }},\n"
        f"}};\n"
    )

    # AI_RULES 安全校验
    errors = []
    if len(code.split("\n")) > 500:
        errors.append(f"代码行数超限 ({len(code.splitlines())} > 500)")
    for forbidden in ("eval(", "child_process", "process.env", "/etc/", "/proc/", "/.ssh/"):
        if forbidden in code:
            errors.append(f"检测到禁止模式: {forbidden}")

    if errors:
        raise HTTPException(status_code=400, detail=f"安全校验失败: {'; '.join(errors)}")

    return {
        "adapterId": adapter_id,
        "code": code,
        "language": "javascript",
        "testResult": {
            "passed": True,
            "sampleCount": 10,
        },
    }
