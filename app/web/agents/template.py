from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import yaml

from app.web.agents.base import BaseAgent

logger = logging.getLogger(__name__)


@dataclass
class FieldDef:
    name: str
    selector: str = ""
    type: str = "text"
    sample: str = ""
    required: bool = False


@dataclass
class AcquisitionConfig:
    mode: str = "ai_analysis"
    api_endpoints: List[str] = field(default_factory=list)
    network_requests: List[str] = field(default_factory=list)


@dataclass
class PaginationAnalysis:
    type: str = "none"
    list_page: str = ""
    start_page: int = 1
    results_per_page: int = 0
    page_param: str = "page"


@dataclass
class AnalysisResult:
    url: str = ""
    base_url: str = ""
    domain: str = ""
    template_name: str = ""
    display_name: str = ""
    root_selector: str = ""
    fields: List[FieldDef] = field(default_factory=list)
    sample_items: List[Dict[str, Any]] = field(default_factory=list)
    pagination: PaginationAnalysis = field(default_factory=PaginationAnalysis)
    mode: str = "ai_analysis"
    template_dict: Dict[str, Any] = field(default_factory=dict)
    template_yaml: str = ""
    adapter_code: str = ""
    warnings: List[str] = field(default_factory=list)
    detail_fields: List[FieldDef] = field(default_factory=list)
    acquisition: AcquisitionConfig = field(default_factory=AcquisitionConfig)


class TemplateAgent(BaseAgent):
    def __init__(self):
        super().__init__()
        self._register_default_prompts()

    def _register_default_prompts(self) -> None:
        self.register_prompt(
            "analyze_page",
            """你是一个专业的网页数据采集分析专家。请分析以下网页内容，识别数据采集所需的关键信息。

            网页URL: {url}
            网页标题: {page_title}
            网页摘要: {page_summary}

            请分析以下内容：
            1. 数据类型（list列表页/detail详情页/form表单页）
            2. 主要数据字段（名称、类型、选择器）
            3. 分页机制（page_number页码/scroll滚动/next_button下一页按钮/none无）
            4. API接口（如果存在动态加载）
            5. 需要下载的资源（图片/文件等）
            6. 数据去重字段

            请输出严格的JSON格式分析结果，不要使用|符号：
            {{
                "data_type": "list",
                "fields": [
                    {{
                        "name": "title",
                        "type": "text",
                        "selector": "h2.title",
                        "description": "标题",
                        "sample_value": "示例标题",
                        "required": true
                    }}
                ],
                "pagination": {{
                    "type": "page_number",
                    "page_param": "page",
                    "list_page": "{url}",
                    "start_page": 1,
                    "results_per_page": 20
                }},
                "api_endpoints": [],
                "download_fields": [],
                "dedup_fields": ["url"],
                "description": "模板描述"
            }}
            """,
        )

        self.register_prompt(
            "generate_template",
            """你是一个专业的爬虫模板生成专家。请根据分析结果生成完整的YAML采集模板。

            要求：
            1. 模板必须符合YAML语法规范
            2. 包含完整的采集配置
            3. 正确配置请求参数、响应解析、字段提取
            4. 配置分页策略
            5. 配置数据去重
            6. 配置资源下载
            7. 模板必须可被适配器解析和执行

            分析结果：
            {analysis_json}

            请输出完整的YAML模板，使用```yaml和```包裹：
            """,
        )

    async def analyze_page(
        self, url: str, html_text: str, network_endpoints: List[str] = None
    ) -> Dict[str, Any]:
        page_title = self._extract_title(html_text)
        page_summary = self._extract_summary(html_text)

        network_endpoints = network_endpoints or []

        prompt = self.render_prompt(
            "analyze_page",
            url=url,
            page_title=page_title,
            page_summary=page_summary,
        )

        result = await self.generate_complete_json(prompt)

        if network_endpoints:
            result.setdefault("api_endpoints", []).extend(network_endpoints[:10])

        return result

    async def generate_template(self, url: str, analysis_result: Dict[str, Any]) -> str:
        analysis_json = json.dumps(analysis_result, ensure_ascii=False, indent=2)

        prompt = self.render_prompt(
            "generate_template",
            analysis_json=analysis_json,
        )

        response = await self.generate(prompt, max_tokens=8192)
        return self._extract_yaml(response)

    def _extract_title(self, html_text: str) -> str:
        match = re.search(r"<title[^>]*>([^<]+)</title>", html_text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return "Unknown"

    def _extract_summary(self, html_text: str) -> str:
        match = re.search(
            r"<meta\s+name=[\"']description[\"']\s+content=[\"']([^\"']+)[\"']",
            html_text,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()

        text = re.sub(r"<[^>]+>", " ", html_text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:200]

    def _extract_yaml(self, response: str) -> str:
        match = re.search(r"```yaml\s*([\s\S]*?)\s*```", response)
        if match:
            return match.group(1).strip()

        match = re.search(r"```\s*([\s\S]*?)\s*```", response)
        if match:
            return match.group(1).strip()

        return response.strip()

    def _build_template_name(self, url: str) -> str:
        parsed = urlparse(url)
        domain = parsed.hostname or "unknown"
        path_parts = [p for p in parsed.path.strip("/").split("/") if p][:3]
        name_parts = [domain.replace(".", "_")] + path_parts
        name = "_".join(name_parts).lower()
        name = re.sub(r"[^a-z0-9_]", "", name)
        return name[:50]

    def _build_display_name(self, url: str) -> str:
        parsed = urlparse(url)
        domain = parsed.hostname or "Unknown"
        return domain.replace("www.", "").replace(".com", "").replace(".cn", "").title()

    def _build_base_url(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.hostname}"

    def _build_inferred_fields(self, analysis_result: Dict[str, Any]) -> List[FieldDef]:
        fields = []
        for field_data in analysis_result.get("fields", []):
            fields.append(
                FieldDef(
                    name=field_data.get("name", ""),
                    selector=field_data.get("selector", ""),
                    type=field_data.get("type", "text"),
                    sample=field_data.get("sample_value", ""),
                    required=field_data.get("required", False),
                )
            )
        return fields

    def _build_sample_items(
        self, fields: List[FieldDef], analysis_result: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        return []

    def _build_pagination(
        self, url: str, analysis_result: Dict[str, Any]
    ) -> PaginationAnalysis:
        pagination_data = analysis_result.get("pagination", {})
        return PaginationAnalysis(
            type=pagination_data.get("type", "none"),
            list_page=pagination_data.get("list_page", url),
            start_page=pagination_data.get("start_page", 1),
            results_per_page=pagination_data.get("results_per_page", 20),
            page_param=pagination_data.get("page_param", "page"),
        )

    def _build_acquisition(
        self, url: str, analysis_result: Dict[str, Any]
    ) -> AcquisitionConfig:
        return AcquisitionConfig(
            mode="ai_analysis",
            api_endpoints=analysis_result.get("api_endpoints", []),
        )


template_agent = TemplateAgent()
