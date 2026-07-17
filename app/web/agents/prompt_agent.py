from __future__ import annotations

import os
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import yaml
from lxml import html as lxml_html

from app.config.settings import settings
from app.downloader.http_client import HttpClient
from app.models.template import SiteTemplate

_MODEL_PATH = Path(__file__).resolve().parents[3] / "models" / "Qwen" / "2.5-0.5B-Instruct"


@dataclass
class InferredField:
    name: str
    field_type: str
    relative_selector: str | None
    sample: str | None
    required: bool = False
    attr_name: str | None = None
    global_selector: str | None = None
    generic_supported: bool = False
    adapter_supported: bool = False

    def response_dict(self, mode: str) -> dict[str, Any]:
        selector = self.global_selector if mode == "generic_template" else self.relative_selector
        field: dict[str, Any] = {
            "name": self.name,
            "selector": selector or "",
            "type": self.field_type,
            "required": self.required,
            "sample": self.sample,
        }
        if self.attr_name:
            field["attrName"] = self.attr_name
        field["scope"] = "page" if mode == "generic_template" else "item"
        field["supportedByTemplate"] = self.generic_supported
        field["supportedByAdapter"] = self.adapter_supported
        return field

    def template_dict(self, selector: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "selector": selector,
            "selector_type": "css",
            "field_type": self.field_type,
            "required": self.required,
        }
        if self.attr_name:
            payload["attr_name"] = self.attr_name
        if self.field_type in {"text", "href", "src", "attr"}:
            payload["transform"] = "strip"
        return payload


@dataclass
class PaginationAnalysis:
    type: str
    list_page: str
    start_page: int
    results_per_page: int
    next_selector: str | None = None
    page_param: str | None = None
    verified_pages: int = 1
    max_pages: int = 1
    exact_last_page: int | None = None
    probes: list[dict[str, Any]] = field(default_factory=list)

    def response_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": self.type,
            "listPage": self.list_page,
            "startPage": self.start_page,
            "resultsPerPage": self.results_per_page,
            "maxPages": self.max_pages,
            "verifiedPages": self.verified_pages,
            "exactLastPage": self.exact_last_page,
            "probes": self.probes,
        }
        if self.next_selector:
            payload["selector"] = self.next_selector
        if self.page_param:
            payload["pageParam"] = self.page_param
        return payload


@dataclass
class AcquisitionAnalysis:
    mode: str
    recommended_transport: str
    endpoint: str = ""
    json_item_path: str = ""
    evidence: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    sample_records: list[dict[str, Any]] = field(default_factory=list, repr=False)

    def response_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "recommendedTransport": self.recommended_transport,
            "endpoint": self.endpoint,
            "jsonItemPath": self.json_item_path,
            "evidence": self.evidence,
            "candidates": self.candidates,
        }


@dataclass
class AnalysisResult:
    url: str
    base_url: str
    domain: str
    template_name: str
    display_name: str
    root_selector: str
    fields: list[InferredField]
    sample_items: list[dict[str, Any]]
    pagination: PaginationAnalysis
    mode: str
    template_dict: dict[str, Any]
    template_yaml: str
    adapter_code: str
    warnings: list[str]
    detail_fields: list[dict[str, Any]]
    acquisition: AcquisitionAnalysis
    analyzed_at: float = field(default_factory=time.time)

    def fields_payload(self) -> list[dict[str, Any]]:
        return [field.response_dict(self.mode) for field in self.fields]


class LocalQwenModel:
    def __init__(self, model_path: Path | None = None) -> None:
        self._model_path = model_path or _MODEL_PATH
        self._model: Any = None
        self._tokenizer: Any = None
        self._load_lock = threading.Lock()

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            if not (self._model_path / "model.safetensors").is_file():
                raise RuntimeError(f"Local Qwen model is incomplete: {self._model_path}")
            from transformers import AutoModelForCausalLM, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(
                str(self._model_path), local_files_only=True
            )
            self._model = AutoModelForCausalLM.from_pretrained(
                str(self._model_path),
                local_files_only=True,
                dtype="auto",
                low_cpu_mem_usage=True,
            )
            self._model.eval()
            self._model.generation_config.do_sample = False
            self._model.generation_config.temperature = None
            self._model.generation_config.top_p = None
            self._model.generation_config.top_k = None

    async def generate(self, prompt: str, max_tokens: int = 4096, temperature: float = 0.1, do_sample: bool = False) -> str:
        import torch

        self._load()

        encoded = self._tokenizer(prompt, return_tensors="pt", truncation=True, max_length=2048)
        with torch.inference_mode():
            if do_sample:
                outputs = self._model.generate(
                    **encoded,
                    max_new_tokens=max_tokens,
                    temperature=temperature,
                    num_return_sequences=1,
                    do_sample=True,
                )
            else:
                outputs = self._model.generate(
                    **encoded,
                    max_new_tokens=max_tokens,
                    num_return_sequences=1,
                    do_sample=False,
                )

        response = self._tokenizer.decode(outputs[0], skip_special_tokens=True)
        return response[len(prompt) :] if prompt in response else response


class PromptAgent:
    def __init__(self) -> None:
        self._model = LocalQwenModel()
        self._prompt_templates = self._load_prompt_templates()

    def _load_prompt_templates(self) -> dict[str, str]:
        templates = {
            "page_analysis": """你是一个专业的网页数据采集分析专家。请分析以下网页内容，识别关键页面元素和数据结构。

目标URL: {url}
页面标题: {page_title}

页面HTML片段（去除脚本和样式后）:
{html_snippet}

页面中可能包含的JavaScript代码片段:
{script_snippets}

浏览器网络请求记录（JSON接口）:
{network_endpoints}

请分析以下内容并以JSON格式输出：

1. **字段识别**：识别页面中的主要数据字段，包括但不限于：
   - 标题/名称
   - 详情页链接
   - 发布日期
   - 摘要/描述
   - 缩略图/图片
   - 点赞数、评论数、下载量、评分等数值型指标
   - 其他与业务相关的字段

2. **分页机制**：分析页面的翻页方式，可能是：
   - 页码式分页（page=1,2,3...）
   - 下一页按钮（Next Page）
   - 无限滚动（滚动加载更多）
   - 偏移量分页（offset/limit）

3. **资源下载**：识别需要下载的资源类型，如图片、文件等

4. **去重字段**：确定用于数据去重的关键字段

5. **API接口**：如果页面使用JavaScript动态加载数据，请识别API接口地址和数据结构

输出格式要求：
```json
{{
  "fields": [
    {{
      "name": "字段名称（英文）",
      "field_type": "字段类型（text/href/src/attr/number/date）",
      "selector": "CSS选择器或JSON路径",
      "selector_type": "css或json",
      "description": "字段描述",
      "required": true/false,
      "sample_value": "示例值"
    }}
  ],
  "pagination": {{
    "type": "page_number/next_button/infinite_scroll/offset",
    "next_selector": "下一页按钮的CSS选择器",
    "page_param": "分页参数名",
    "list_page_pattern": "列表页URL模式",
    "start_page": 起始页码,
    "results_per_page": 每页结果数,
    "max_pages": 最大页数
  }},
  "resources": [
    {{
      "field_name": "资源字段名",
      "asset_type": "资源类型（image/attachment/thumbnail）",
      "selector": "选择器",
      "link_type": "src或href",
      "description": "资源描述"
    }}
  ],
  "dedup": {{
    "fields": ["去重字段名列表"],
    "strategy": "exact"
  }},
  "api_endpoint": "API接口地址",
  "api_item_path": "JSON数据路径",
  "analysis_context": {{
    "data_type": "数据类型",
    "page_structure": "页面结构描述",
    "dynamic_loading": true/false
  }}
}}
```

请确保输出的JSON格式正确，不要包含其他文字。
""",
            "template_generation": """你是一个专业的爬虫模板生成专家。请根据以下分析结果生成完整的采集模板。

目标URL: {url}
分析结果:
{analysis_result}

请生成一个完整的YAML格式采集模板，包含以下内容：
1. 基本信息（name, display_name, base_url, data_type, description）
2. 请求配置（list_page, list_request）
3. 字段配置（list_fields）
4. 分页配置（list_pagination）
5. 去重配置（dedup_fields）
6. 资源下载配置（download）

输出格式要求：
```yaml
name: 模板名称
display_name: 显示名称
base_url: 基础URL
data_type: 数据类型
description: 模板描述
response_type: html或json
json_item_path: JSON数据路径（如果是json类型）
list_page: 列表页URL
list_request:
  method: GET
  headers:
    User-Agent: Mozilla/5.0...
  encoding: utf-8
dedup_fields:
  - url
list_fields:
  - name: title
    selector: CSS选择器
    selector_type: css
    field_type: text
    required: true
list_pagination:
  type: page_number
  page_param: page
  start_page: 1
  max_pages: 100
download:
  - selector: thumbnail
    selector_type: json
    link_type: src
    asset_type: thumbnail
```

请确保输出的YAML格式正确。
""",

            "prompt_optimization": """你是一个专业的Prompt优化专家。请分析以下Prompt并进行优化。

原始Prompt:
{original_prompt}

优化目标: {optimization_goal}

请提供优化后的Prompt，并说明优化理由。

输出格式要求：
```json
{{
  "optimized_prompt": "优化后的Prompt内容",
  "optimization_reasons": ["优化理由1", "优化理由2"],
  "suggestion": "其他建议"
}}
```
""",
        }
        return templates

    def get_prompt_template(self, name: str) -> str:
        if name not in self._prompt_templates:
            raise ValueError(f"Prompt template '{name}' not found")
        return self._prompt_templates[name]

    def register_prompt_template(self, name: str, content: str) -> None:
        self._prompt_templates[name] = content

    async def analyze_page(self, url: str, html: str, network_endpoints: list[str] | None = None) -> dict[str, Any]:
        page_title = self._extract_page_title(html)
        html_snippet = self._extract_html_snippet(html)
        script_snippets = self._extract_script_snippets(html)

        prompt = self.get_prompt_template("page_analysis").format(
            url=url,
            page_title=page_title,
            html_snippet=html_snippet,
            script_snippets=script_snippets,
            network_endpoints="\n".join(network_endpoints or []),
        )

        response = await self._model.generate(prompt)
        return self._parse_json_response(response)

    async def generate_template(self, url: str, analysis_result: dict[str, Any]) -> str:
        prompt = self.get_prompt_template("template_generation").format(
            url=url,
            analysis_result=json.dumps(analysis_result, ensure_ascii=False, indent=2),
        )

        response = await self._model.generate(prompt)
        yaml_match = re.search(r"```yaml\s*([\s\S]*?)\s*```", response)
        if yaml_match:
            return yaml_match.group(1)
        return response

    async def optimize_prompt(self, original_prompt: str, goal: str = "") -> dict[str, Any]:
        prompt = self.get_prompt_template("prompt_optimization").format(
            original_prompt=original_prompt,
            optimization_goal=goal or "提高分析准确率和召回率",
        )

        response = await self._model.generate(prompt)
        return self._parse_json_response(response)

    async def analyze(self, url: str, prompt: str = "") -> AnalysisResult:
        html = await self._fetch_page(url)
        return await self.analyze_html(url, html, prompt)

    async def analyze_html(
        self,
        url: str,
        html_text: str,
        prompt: str = "",
        network_endpoints: list[str] | None = None,
    ) -> AnalysisResult:
        analysis_result = await self.analyze_page(url, html_text, network_endpoints)

        template_yaml = await self.generate_template(url, analysis_result)
        template_name = self._build_template_name(url)
        
        try:
            template_dict = yaml.safe_load(template_yaml)
        except yaml.YAMLError:
            template_dict = {}

        from app.web.agents.adapter_agent import adapter_agent
        adapter_result = await adapter_agent.generate_adapter(template_name, template_yaml)
        adapter_code = adapter_result.adapter_code

        fields = self._build_inferred_fields(analysis_result)
        sample_items = self._build_sample_items(fields, analysis_result)
        pagination = self._build_pagination(url, analysis_result)
        acquisition = self._build_acquisition(url, analysis_result)

        return AnalysisResult(
            url=url,
            base_url=self._build_base_url(url),
            domain=urlparse(url).hostname or "",
            template_name=template_name,
            display_name=self._build_display_name(url),
            root_selector="",
            fields=fields,
            sample_items=sample_items,
            pagination=pagination,
            mode="ai_analysis",
            template_dict=template_dict,
            template_yaml=template_yaml,
            adapter_code=adapter_code,
            warnings=[],
            detail_fields=[],
            acquisition=acquisition,
        )

    async def _fetch_page(self, url: str) -> str:
        client = HttpClient()
        try:
            return await client.request_page(url)
        finally:
            await client.close()

    def _parse_json_response(self, response: str) -> dict[str, Any]:
        json_match = re.search(r"```json\s*([\s\S]*?)\s*```", response)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        try:
            return json.loads(response)
        except json.JSONDecodeError:
            return {}

    def _build_inferred_fields(self, analysis: dict[str, Any]) -> list[InferredField]:
        fields = []
        for field_data in analysis.get("fields", []):
            fields.append(InferredField(
                name=field_data.get("name", ""),
                field_type=field_data.get("field_type", "text"),
                relative_selector=field_data.get("selector"),
                sample=field_data.get("sample_value"),
                required=field_data.get("required", False),
                generic_supported=True,
                adapter_supported=True,
            ))
        return fields

    def _build_sample_items(self, fields: list[InferredField], analysis: dict[str, Any]) -> list[dict[str, Any]]:
        items = []
        for _ in range(3):
            item = {}
            for field in fields:
                item[field.name] = field.sample or f"示例{field.name}"
            items.append(item)
        return items

    def _build_pagination(self, url: str, analysis: dict[str, Any]) -> PaginationAnalysis:
        pagination_data = analysis.get("pagination", {})
        return PaginationAnalysis(
            type=pagination_data.get("type", "page_number"),
            list_page=pagination_data.get("list_page_pattern", url),
            start_page=pagination_data.get("start_page", 1),
            results_per_page=pagination_data.get("results_per_page", 20),
            next_selector=pagination_data.get("next_selector"),
            page_param=pagination_data.get("page_param"),
            max_pages=pagination_data.get("max_pages", 100),
        )

    def _build_acquisition(self, url: str, analysis: dict[str, Any]) -> AcquisitionAnalysis:
        api_endpoint = analysis.get("api_endpoint")
        if api_endpoint:
            return AcquisitionAnalysis(
                mode="api",
                recommended_transport="json_api",
                endpoint=api_endpoint,
                json_item_path=analysis.get("api_item_path", ""),
                evidence=["Detected by AI analysis"],
            )
        return AcquisitionAnalysis(
            mode="html",
            recommended_transport="html",
            endpoint=url,
            evidence=["HTML page analysis"],
        )

    @staticmethod
    def _extract_page_title(html: str) -> str:
        match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _extract_html_snippet(html: str) -> str:
        body_match = re.search(r"<body[^>]*>([\s\S]*?)</body>", html, re.IGNORECASE)
        if body_match:
            snippet = body_match.group(1)
            snippet = re.sub(r"<script[^>]*>[\s\S]*?</script>", "", snippet, flags=re.IGNORECASE)
            snippet = re.sub(r"<style[^>]*>[\s\S]*?</style>", "", snippet, flags=re.IGNORECASE)
            snippet = re.sub(r"\s+", " ", snippet)
            return snippet[:2000]
        return html[:2000]

    @staticmethod
    def _extract_script_snippets(html: str) -> str:
        scripts = re.findall(r"<script[^>]*>([\s\S]*?)</script>", html, re.IGNORECASE)
        filtered = []
        for script in scripts:
            if len(script) > 50 and not script.strip().startswith("<!--"):
                filtered.append(script[:500])
        return "\n---\n".join(filtered[:5])

    @staticmethod
    def _build_template_name(url: str) -> str:
        parsed = urlparse(url)
        hostname = parsed.hostname or "unknown"
        cleaned = re.sub(r"[^a-z0-9]", "_", hostname.replace("www.", ""))
        return cleaned.lower()[:63]

    @staticmethod
    def _build_display_name(url: str) -> str:
        parsed = urlparse(url)
        hostname = parsed.hostname or "Unknown"
        return hostname.replace("www.", "").replace(".com", "").replace(".cn", "").title()

    @staticmethod
    def _build_base_url(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"


prompt_agent = PromptAgent()