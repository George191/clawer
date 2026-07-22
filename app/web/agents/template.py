from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List
from urllib.parse import urlparse

import yaml
from lxml import etree
from lxml import html as lxml_html
from pydantic import ValidationError

from app.config.ai_settings import ai_settings
from app.logger import get_logger
from app.models.template import SiteTemplate
from app.web.agents.base import BaseAgent

logger = get_logger(__name__)

_SITE_TEMPLATE_YAML_SHAPE = """
Follow the repository's historical template layout below. Keep this top-level order,
keep request/field/download keys at the shown nesting level, and omit optional sections
when there is no verified evidence. Do not emit empty request/detail/pagination mappings.

name: template_name
display_name: Display Name
base_url: https://example.com
data_type: analyzed_snake_case_type
adapter: ""
anti_crawl_enabled: null
description: "One concise sentence."
response_type: json
json_item_path: data.items
json_total_path: data.total
list_page: /api/items
list_request:
  method: GET
  headers: {}
  encoding: utf-8
dedup_fields:
  - title
list_fields:
  - name: title
    selector: title
    selector_type: json
    field_type: text
    required: true
    description: "Source title."
list_pagination:
  type: page_number
  page_param: page
  start_page: 1
  max_pages: 100
  results_per_page: 20
download:
  - selector: download_url
    selector_type: json
    link_type: href
    asset_type: attachment
    description: "Download URL."

Allowed values:
- response_type: html | json
- selector_type: css | xpath | regex | json
- field_type/link_type: text | attr | html | href | src | json | number | boolean
- list_pagination.type: next_page | page_number | load_more | infinite_scroll
params is a list of mappings with name/description/default/required. batch_params is a
separate top-level mapping with file_path/param_name/start_line/limit/delay. Emit either
only when analysis requires them. For a non-paginated source omit list_pagination. Omit
download and all detail fields when unused instead of emitting empty mappings or lists.
""".strip()


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
    network_responses: List[Dict[str, Any]] = field(default_factory=list)
    page_warnings: List[str] = field(default_factory=list)
    fallback_endpoints: List[str] = field(default_factory=list)


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
        model_path = Path(__file__).parents[3] / "models" / "Qwen" / "2.5-3B-Instruct"
        super().__init__(str(model_path))
        self._register_default_prompts()

    def _register_default_prompts(self) -> None:
        self.register_prompt(
            "analyze_page",
            """
                You analyze web pages for this project's template-driven crawler.

                Target URL: {url}
                Page title: {page_title}
                Page summary: {page_summary}
                Compact rendered structure: {page_structure}
                Observed network evidence: {network_evidence}
                Preflight warnings: {page_warnings}

                Work in this order:
                1. API discovery: inspect successful XHR/fetch evidence first. If a verified API supplies the records, select it as the primary source. Analyze rendered HTML only when no usable API exists. A page URL ending in query/results may be only a JavaScript shell.
                2. Source validation: require a successful status, plausible content type, a record container and at least one real sample. Reject maintenance, CAPTCHA, login and WAF bodies. HTML from an endpoint is valid only when it contains the intended records, not an error shell.
                3. Data-type field contract: infer data_type only from the page title, rendered structure, record fields and API samples. Return a concise lowercase snake_case singular business entity type; do not choose from a fixed enum and do not infer it from the hostname alone. Keep the smallest set that can identify and reconstruct the business record. Preserve source values exactly at collection time; do not synthesize derived values in the template or adapter. For navwarn consider the source warning number, source issue time, category/region/status, location coordinates and full warning text. Treat ingest/transport timestamps separately from business issue time.
                4. Exclusions: remove transport/UI/internal fields such as request IDs, row IDs, component IDs, ranks and duplicated aliases unless they are the only stable business identity. Never keep two fields with the same meaning (for example navArea/usNavArea/msgType or msgSqncNumber/sequenceNumber); choose one canonical snake_case output name and document exactly one verified source field. A template targets one verified site schema: do not propose runtime alias lists or fallback source keys. If the source schema changes, require new evidence and update the selector.
                5. Deduplication: prove the smallest stable business key. Prefer one immutable field such as canonical URL, publication number or warning number scoped by area. Use multiple fields only when one field is not unique; do not add redundant backup fields to a unique key.
                6. Resources: identify downloadable or media values separately. Classify cover/thumbnail, body image/figure, attachment/document, gallery/slide, video/audio and dataset/archive fields. State whether each comes from list, detail or API and whether adapter enrichment is required.
                7. Acquisition details: convert variable path/query values into required params. Set pagination to none when the verified API returns the complete collection; do not fabricate page parameters or page loops. Record an official fallback only when evidence supports it. Zero verified records is a warning, not collection success.

                Return strict JSON without markdown:
                {{
                    "data_type": "business entity type inferred from page evidence",
                    "source_kind": "api|html|text|dynamic_shell|unavailable",
                    "selected_endpoint": "",
                    "json_item_path": "",
                    "verified_record_count": 0,
                    "fields": [{{"name":"title","source_field":"title","type":"text","selector":"title","business_role":"content","description":"Title","sample_value":"","required":true}}],
                    "excluded_fields": [{{"source_field":"rowId","reason":"internal UI identifier","duplicate_of":""}}],
                    "dedup_analysis": {{"fields":[],"reason":"","uniqueness_scope":""}},
                    "pagination": {{"type":"none","page_param":"page","list_page":"{url}","start_page":1,"results_per_page":0}},
                    "api_endpoints": [],
                    "fallback_endpoints": [],
                    "response_evidence": [],
                    "warnings": [],
                    "resource_fields": [{{"name":"thumbnail","asset_type":"thumbnail","source":"list|detail|api","selector":"","multiple":false,"requires_adapter":false}}],
                    "adapter_requirements": [],
                    "download_fields": [],
                    "dedup_fields": [],
                    "description": ""
                }}
            """,
        )

        self.register_prompt(
            "generate_template",
            """
                Generate a complete YAML template for this project's SiteTemplate schema.

                Rules:
                1. Use selected_endpoint when it is a verified API; use the rendered page only when analysis found no usable API. Match response_type, json_item_path and selector_type to the selected response.
                2. Generate list_fields from the approved fields only. Do not reintroduce excluded transport/UI IDs or duplicate aliases. Output canonical snake_case names while each selector retains exactly one observed source field. Never put source-field fallback, alias guessing or derived-value synthesis in adapter code.
                3. Set dedup_fields to exactly the minimal stable source/identity fields in dedup_analysis/dedup_fields. Every dedup field must be produced directly by list_fields; do not depend on downstream ODS normalization. Use a composite key only when its stated scope requires it.
                4. Convert variable URL/query values into params and mark inputs required unless evidence proves a safe default. Preserve request method, required headers, pagination and verified official fallback behavior.
                5. Translate every resource_fields/download_fields entry into a valid download item with selector_type, link_type and asset_type. If a resource requires detail/API enrichment, add detail_page/detail_fields where generic parsing is enough; otherwise set adapter and explain the exact required output field in description. News templates must account for available cover/thumbnail, body images and attachments without duplicating the cover in images.
                6. Keep business record fields separate from resources. A source media ID used only to resolve a URL is adapter input, not a final resource selector; the adapter must output the actual URL/list expected by download.selector.
                7. For a verified non-paginated API, omit list_pagination entirely. For a verified paginated API, include only the observed page parameter and bounds. If verified_record_count is zero or source_kind is unavailable, do not fabricate selectors or samples; retain warnings and require guarded adapter validation.
                8. Use only SiteTemplate schema fields: name, display_name, base_url, data_type, adapter, anti_crawl_enabled, description, params/batch_params, response_type/json paths, list_page/list_request/list_fields/dedup_fields/list_pagination, detail_page/detail_request/detail_fields and download.
                9. Set data_type exactly to the analyzed data_type. Do not reclassify it during YAML generation.
                10. Keep description to one concise sentence describing the source and collection scope. Do not include reasoning, warnings, field inventories or implementation details.
                11. Every named SiteTemplate field is a top-level sibling. Never nest params, response_type, list_page, list_fields, list_pagination, detail_page or download inside one another. Never replace a scalar or list with a mapping.

                Exact YAML field shapes and enums:
                {schema_shape}

                Target URL: {url}
                Required template name: {template_name}
                Required display name: {display_name}
                If adapter is required, its name must be exactly {template_name}; otherwise leave adapter empty.

                Existing same data-type template conventions:
                {reference_templates}

                Use references only for project naming and schema conventions. Current verified evidence wins. Never copy a reference endpoint, selector, field or composite dedup key unless the current analysis independently supports it.

                Analysis result:
                {analysis_json}

                Return only a fenced ```yaml block.
            """,
        )

        self.register_prompt(
            "repair_template",
            """
                Repair the YAML so it validates against this project's SiteTemplate schema.

                Requirements:
                1. Preserve evidence-backed endpoints, selectors, fields, values and data_type from the broken YAML and analysis. Do not invent new source evidence.
                2. Fix only schema shape, nesting, required fields and enum values. All SiteTemplate fields are top-level siblings.
                3. Return a complete template, not a patch or explanation.

                Exact YAML field shapes and enums:
                {schema_shape}

                Validation errors:
                {validation_errors}

                Broken YAML:
                {template_yaml}

                Analysis result:
                {analysis_json}

                Return only a fenced ```yaml block.
            """,
        )

    async def analyze_page(
        self,
        url: str,
        html_text: str,
        network_endpoints: List[str] = None,
        network_responses: List[Dict[str, Any]] | None = None,
        page_warnings: List[str] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        page_title = self._extract_title(html_text)
        page_summary = self._extract_summary(html_text)
        page_structure = self._extract_structure(html_text)

        network_endpoints = network_endpoints or []
        network_responses = network_responses or []
        page_warnings = page_warnings or []
        prioritized_responses = sorted(
            network_responses,
            key=lambda response: (
                not bool(response.get("recordFields")),
                int(response.get("status") or 0) >= 400,
                str(response.get("resourceType") or "") not in {"xhr", "fetch"},
            ),
        )
        prompt_evidence = [
            {
                "url": response.get("url"),
                "status": response.get("status"),
                "contentType": response.get("contentType"),
                "resourceType": response.get("resourceType"),
                "jsonItemPath": response.get("jsonItemPath"),
                "recordFields": list(response.get("recordFields") or [])[:40],
                "sampleRecord": self._compact_value(
                    dict(list((response.get("sampleRecord") or {}).items())[:8])
                    if isinstance(response.get("sampleRecord"), dict)
                    else {}
                ),
                "bodyPreview": str(response.get("bodyPreview") or "")[:300],
                "links": list(response.get("links") or [])[:5],
            }
            for response in prioritized_responses[:3]
        ]

        prompt = self.render_prompt(
            "analyze_page",
            url=url,
            page_title=page_title,
            page_summary=page_summary,
            page_structure=page_structure,
            network_evidence=json.dumps(prompt_evidence, ensure_ascii=False),
            page_warnings=json.dumps(page_warnings, ensure_ascii=False),
        )

        result = await self.generate_complete_json(
            prompt,
            max_retries=max(1, ai_settings.llm_max_retries),
            max_tokens=3072,
            on_event=on_event,
        )

        if network_endpoints:
            result.setdefault("api_endpoints", []).extend(network_endpoints[:10])
        result.setdefault("response_evidence", []).extend(network_responses[:30])
        result.setdefault("warnings", []).extend(page_warnings)

        return result

    async def generate_template(
        self,
        url: str,
        analysis_result: Dict[str, Any],
        page_title: str = "",
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        analysis_context = dict(analysis_result)
        analysis_context["response_evidence"] = [
            {
                "url": response.get("url"),
                "status": response.get("status"),
                "contentType": response.get("contentType"),
                "jsonItemPath": response.get("jsonItemPath"),
                "recordFields": list(response.get("recordFields") or [])[:100],
            }
            for response in (analysis_result.get("response_evidence") or [])[:6]
            if isinstance(response, dict)
        ]
        analysis_json = json.dumps(analysis_context, ensure_ascii=False, indent=2)
        reference_templates = self._reference_template_summaries(
            str(analysis_result.get("data_type") or "other"),
            self._analysis_response_type(analysis_result),
        )

        prompt = self.render_prompt(
            "generate_template",
            url=url,
            template_name=self._build_template_name(url),
            display_name=self._build_display_name(url, page_title),
            analysis_json=analysis_json,
            reference_templates=json.dumps(reference_templates, ensure_ascii=False, indent=2),
            schema_shape=_SITE_TEMPLATE_YAML_SHAPE,
        )

        response = await self.generate(
            prompt,
            max_tokens=4096,
        )
        template_yaml = self._normalize_template_yaml(
            url,
            self._extract_yaml(response),
            analysis_result,
            page_title,
        )
        validation_error = self._template_validation_error(template_yaml)
        for attempt in range(max(1, ai_settings.llm_max_retries)):
            if not validation_error:
                break
            if on_event:
                on_event({
                    "kind": "retry",
                    "attempt": attempt + 2,
                    "reason": "template_schema_validation",
                    "content": validation_error[:2000],
                })
            repair_prompt = self.render_prompt(
                "repair_template",
                schema_shape=_SITE_TEMPLATE_YAML_SHAPE,
                validation_errors=validation_error[:4000],
                template_yaml=template_yaml,
                analysis_json=analysis_json,
            )
            repaired_response = await self.generate(repair_prompt, max_tokens=4096)
            template_yaml = self._normalize_template_yaml(
                url,
                self._extract_yaml(repaired_response),
                analysis_result,
                page_title,
            )
            validation_error = self._template_validation_error(template_yaml)
        if on_event:
            on_event({
                "kind": "template_delta",
                "content": "Template generated",
                "templateYaml": template_yaml,
            })
        return template_yaml

    @staticmethod
    def _template_validation_error(template_yaml: str) -> str:
        try:
            template = yaml.safe_load(template_yaml)
        except yaml.YAMLError as exc:
            return f"Invalid YAML: {exc}"
        if not isinstance(template, dict):
            return "Template must be a YAML mapping"
        try:
            SiteTemplate.model_validate(template)
        except ValidationError as exc:
            return str(exc)
        return ""

    @staticmethod
    def _reference_template_summaries(
        data_type: str,
        response_type: str,
    ) -> list[dict[str, Any]]:
        template_dir = Path(__file__).parents[3] / "templates"
        candidates: list[dict[str, Any]] = []
        for path in sorted(template_dir.glob("*.y*ml")):
            try:
                template = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(template, dict):
                continue

            def field_names(values: Any) -> list[str]:
                if not isinstance(values, list):
                    return []
                return [
                    str(item.get("name"))
                    for item in values
                    if isinstance(item, dict) and item.get("name")
                ]

            downloads = template.get("download") or []
            if isinstance(downloads, dict):
                downloads = [downloads]
            list_fields = template.get("list_fields") or []
            first_list_field = next(
                (item for item in list_fields if isinstance(item, dict)),
                {},
            )
            pagination = template.get("list_pagination")
            first_download = next(
                (item for item in downloads if isinstance(item, dict)),
                {},
            )
            candidates.append(
                {
                    "name": template.get("name"),
                    "data_type": template.get("data_type"),
                    "adapter": template.get("adapter"),
                    "response_type": template.get("response_type", "html"),
                    "top_level_order": list(template.keys()),
                    "list_request_fields": list((template.get("list_request") or {}).keys()),
                    "list_field_fields": list(first_list_field.keys()),
                    "list_pagination_fields": (
                        list(pagination.keys()) if isinstance(pagination, dict) else []
                    ),
                    "download_fields": list(first_download.keys()),
                    "params": [
                        {
                            "name": item.get("name"),
                            "required": item.get("required", True),
                            "has_default": item.get("default") is not None,
                        }
                        for item in (template.get("params") or [])
                        if isinstance(item, dict)
                    ],
                    "list_fields": field_names(template.get("list_fields") or []),
                    "detail_fields": field_names(template.get("detail_fields") or []),
                    "dedup_fields": list(template.get("dedup_fields") or []),
                    "resources": [
                        {
                            "selector": item.get("selector"),
                            "asset_type": item.get("asset_type", "asset"),
                        }
                        for item in downloads
                        if isinstance(item, dict)
                    ],
                }
            )
        candidates.sort(
            key=lambda item: (
                item.get("response_type") != response_type,
                item.get("data_type") != data_type,
                str(item.get("name") or ""),
            )
        )
        return candidates[:3]

    @staticmethod
    def _analysis_response_type(analysis_result: Dict[str, Any]) -> str:
        selected_endpoint = str(analysis_result.get("selected_endpoint") or "")
        for response in analysis_result.get("response_evidence") or []:
            if not isinstance(response, dict):
                continue
            if selected_endpoint and str(response.get("url") or "") != selected_endpoint:
                continue
            if "json" in str(response.get("contentType") or "").lower():
                return "json"
        return "json" if analysis_result.get("json_item_path") else "html"

    @classmethod
    def _compact_value(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value[:160]
        if isinstance(value, list):
            return [cls._compact_value(item) for item in value[:3]]
        if isinstance(value, dict):
            return {
                str(key): cls._compact_value(item)
                for key, item in list(value.items())[:40]
            }
        return value

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

    def _extract_structure(self, html_text: str) -> str:
        try:
            document = lxml_html.fromstring(html_text)
        except (ValueError, etree.ParserError):
            text = re.sub(r"<[^>]+>", " ", html_text)
            return re.sub(r"\s+", " ", text).strip()[:1500]

        lines: list[str] = []
        seen: set[str] = set()
        candidates = document.xpath(
            "//h1 | //h2 | //h3 | //article | //li | //a[@href] | "
            "//*[contains(translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'card')] | "
            "//*[contains(translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'item')] | "
            "//*[contains(translate(@class, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'list')]"
        )
        for element in candidates:
            text = re.sub(r"\s+", " ", element.text_content()).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            attributes = []
            for name in ("class", "href", "data-id", "data-type"):
                value = element.get(name)
                if value:
                    attributes.append(f"{name}={value[:160]}")
            suffix = f" ({', '.join(attributes)})" if attributes else ""
            lines.append(f"<{element.tag}>{suffix} {text[:240]}")
            if len(lines) >= 50:
                break
        return "\n".join(lines)[: min(ai_settings.max_html_chars_for_llm, 3500)]

    def _extract_yaml(self, response: str) -> str:
        text = response.strip()
        opening_fence = re.search(
            r"```[ \t]*(?:yaml|yml)?[ \t]*(?:\r?\n)?",
            text,
            re.IGNORECASE,
        )
        if not opening_fence:
            return text

        yaml_text = text[opening_fence.end():]
        closing_fence = yaml_text.find("```")
        if closing_fence >= 0:
            yaml_text = yaml_text[:closing_fence]
        return yaml_text.strip()

    def _build_template_name(self, url: str) -> str:
        parsed = urlparse(url)
        domain_parts = (parsed.hostname or "unknown").lower().split(".")
        if domain_parts and domain_parts[0] == "www":
            domain_parts = domain_parts[1:]
        two_part_suffixes = {"co.uk", "com.au", "com.cn", "com.hk", "co.jp"}
        suffix_length = 2 if ".".join(domain_parts[-2:]) in two_part_suffixes else 1
        brand_index = max(0, len(domain_parts) - suffix_length - 1)
        brand = domain_parts[brand_index]
        subdomains = domain_parts[:brand_index]
        path_parts = [p for p in parsed.path.strip("/").split("/") if p][:3]
        name_parts = [brand, *subdomains, *path_parts]
        name = "_".join(name_parts).lower()
        name = re.sub(r"[^a-z0-9_]", "", name)
        return name[:50]

    def _build_display_name(self, url: str, page_title: str = "") -> str:
        if page_title.strip():
            title = re.split(r"\s*(?:[|｜·]|\s[-–—]\s)\s*", page_title.strip(), maxsplit=1)[0]
            if title and len(title) <= 60:
                return title
        parsed = urlparse(url)
        domain = parsed.hostname or "Unknown"
        return domain.replace("www.", "").replace(".com", "").replace(".cn", "").title()

    def _normalize_template_yaml(
        self,
        url: str,
        template_yaml: str,
        analysis_result: Dict[str, Any],
        page_title: str,
    ) -> str:
        try:
            template = yaml.safe_load(template_yaml)
        except yaml.YAMLError:
            return template_yaml
        if not isinstance(template, dict):
            return template_yaml

        template_name = self._build_template_name(url)
        template["name"] = template_name
        template["display_name"] = self._build_display_name(url, page_title)
        template["data_type"] = str(analysis_result.get("data_type") or "other")
        if analysis_result.get("adapter_requirements"):
            template["adapter"] = template_name
        else:
            template["adapter"] = ""

        description = re.sub(r"\s+", " ", str(template.get("description") or "")).strip()
        if description:
            first_sentence = re.split(r"(?<=[。！？.!?])", description, maxsplit=1)[0]
            template["description"] = first_sentence[:120].rstrip(" ,，;；")
        else:
            template["description"] = ""

        if template.get("detail_page") == {}:
            template["detail_page"] = None
        if template.get("list_pagination") == {}:
            template.pop("list_pagination")
        if template.get("download") == {}:
            template["download"] = []

        return yaml.safe_dump(template, allow_unicode=True, sort_keys=False).strip()

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
            network_responses=analysis_result.get("response_evidence", []),
            page_warnings=analysis_result.get("warnings", []),
            fallback_endpoints=analysis_result.get("fallback_endpoints", []),
        )


template_agent = TemplateAgent()
