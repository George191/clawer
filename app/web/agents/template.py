from __future__ import annotations

import json
import re
import textwrap
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
params:
  - name: query
    description: "Required query value."
    default: null
    required: true
list_page: /api/items
list_request:
  method: GET
  headers: {}
  encoding: utf-8
list_pagination:
  type: page_number
  page_param: page
  start_page: 1
  max_pages: 100
  results_per_page: 20
response_type: json
json_item_path: data.items
json_total_path: data.total
list_fields:
  - name: title
    selector: title
    selector_type: json
    field_type: text
    required: true
    description: "Source title."
dedup_fields:
  - title
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
            "generate_template",
            """
                Orchestrate one complete SiteTemplate analysis and return its final YAML.

                Execute these stages in order. At each stage, verify the stated checks before continuing. If a check fails, revisit the captured evidence, correct the current stage and every dependent later stage, then verify again. Do not emit reasoning or intermediate documents.

                Stage 1 - Site
                - Derive name, display_name, base_url and one lowercase snake_case business data_type from the target URL, title, page summary and rendered structure.
                - Set description to one concise description of the current page based on its title, meta description and visible content; do not describe the analysis process.
                - When data_type is game, set adapter exactly to {template_name}. For other data types, set adapter only when verified enrichment requires it.
                - Reject maintenance, login, CAPTCHA and loading-shell content as business evidence.

                Stage 2 - Request and params
                - Inspect successful XHR/fetch evidence inside this stage; API discovery is not a separate task or output.
                - Prefer a successful structured response with a record container and real sample. Use rendered HTML only when no usable API evidence exists.
                - Set list_page/list_request from the verified source. Convert variable path/query values, including the input page value, into params. Never fabricate pagination, headers, methods or fallback endpoints.
                - Verify the selected request matches one captured URL and its observed method/parameters.

                Stage 3 - Response
                - Set response_type and JSON paths/selectors from the selected response body shape.
                - Verify status/content type, record container, sample record and response_type agree. An HTML error body from an API URL is not valid API evidence.

                Stage 4 - Fields
                - Generate list_fields/detail_fields only from the selected response sample or rendered structure.
                - Preserve source values. Use one observed selector/source key per canonical output field; exclude UI IDs, ranks, duplicated aliases and derived values.
                - Verify every required selector exists in the captured evidence and every output name is unique.

                Stage 5 - Dedup and download
                - Choose the smallest stable business identity from produced fields. Every dedup field must be emitted by list_fields/detail_fields.
                - Map verified media/attachment URLs to download. Keep cover, body images and attachments distinct. Set adapter only when verified enrichment cannot be expressed by the generic template engine.
                - Verify every download selector is produced and points to an actual URL/list rather than an internal media ID.

                Final verification
                - Use only fields from the SiteTemplate schema below; all named fields are top-level siblings.
                - For a verified non-paginated source omit list_pagination. Never invent records, selectors, aliases or defaults when evidence is missing.
                - Keep description to one concise sentence.
                - Order YAML keys by the stages above so the UI can render Site, Request/Params, Response, Fields, then Dedup/Download progressively.

                Exact YAML field shapes and enums:
                {schema_shape}

                Target URL: {url}
                Required template name: {template_name}
                Required display name: {display_name}
                If data_type is game or an adapter is otherwise required, its name must be exactly {template_name}; otherwise leave adapter empty.

                Existing project conventions:
                {reference_templates}

                Captured page and network evidence:
                {analysis_json}

                User refinement request:
                {user_request}

                Existing template to preserve unless the request or current evidence requires a change:
                {existing_template_yaml}

                Previous attempt requiring rework:
                {previous_template_yaml}

                Previous validation error:
                {validation_error}

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
        user_request: str = "",
    ) -> Dict[str, Any]:
        result = self.build_page_evidence(
            url,
            html_text,
            network_endpoints,
            network_responses,
            page_warnings,
        )
        if on_event:
            on_event({
                "kind": "evidence_ready",
                "content": "Page and network evidence prepared for template orchestration",
            })
        return result

    def build_page_evidence(
        self,
        url: str,
        html_text: str,
        network_endpoints: List[str] | None = None,
        network_responses: List[Dict[str, Any]] | None = None,
        page_warnings: List[str] | None = None,
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
        response_evidence = [
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
                "bodyPreview": str(response.get("bodyPreview") or "")[:800],
                "links": list(response.get("links") or [])[:5],
            }
            for response in prioritized_responses[:6]
        ]
        selected_response = next(
            (
                response
                for response in response_evidence
                if int(response.get("status") or 0) < 400
                and response.get("recordFields")
            ),
            None,
        )
        return {
            "target_url": url,
            "page_title": page_title,
            "page_summary": page_summary,
            "page_structure": page_structure,
            "source_kind": "api" if selected_response else "html",
            "selected_endpoint": str((selected_response or {}).get("url") or ""),
            "json_item_path": str((selected_response or {}).get("jsonItemPath") or ""),
            "selected_candidate": selected_response or {},
            "api_endpoints": list(dict.fromkeys(network_endpoints))[:20],
            "response_evidence": response_evidence,
            "warnings": page_warnings,
        }

    async def generate_template(
        self,
        url: str,
        analysis_result: Dict[str, Any],
        page_title: str = "",
        on_event: Callable[[dict[str, Any]], None] | None = None,
        user_request: str = "",
        existing_template_yaml: str = "",
    ) -> str:
        analysis_context = self._prompt_analysis_context(analysis_result)
        analysis_json = json.dumps(
            analysis_context,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        existing_template = self._safe_template_mapping(existing_template_yaml)
        reference_data_type = str(
            analysis_result.get("data_type")
            or existing_template.get("data_type")
            or "other"
        )
        reference_templates = (
            self._reference_template_summaries(
                reference_data_type,
                self._analysis_response_type(analysis_result),
            )
            if reference_data_type != "other"
            else []
        )
        template_yaml = ""
        validation_error = ""
        previous_template_yaml = "(none)"
        attempts = max(1, ai_settings.llm_max_retries)
        for attempt in range(attempts):
            if attempt and on_event:
                on_event({
                    "kind": "retry",
                    "attempt": attempt + 1,
                    "reason": "template_schema_validation",
                    "content": validation_error[:2000],
                })
            prompt = textwrap.dedent(
                self.render_prompt(
                    "generate_template",
                    url=url,
                    template_name=self._build_template_name(url),
                    display_name=self._build_display_name(url, page_title),
                    analysis_json=analysis_json,
                    reference_templates=json.dumps(reference_templates, ensure_ascii=False),
                    schema_shape=_SITE_TEMPLATE_YAML_SHAPE,
                    user_request=user_request or "(none)",
                    existing_template_yaml=existing_template_yaml or "(none)",
                    previous_template_yaml=previous_template_yaml,
                    validation_error=validation_error or "(none)",
                )
            ).strip()
            streamed_response: list[str] = []
            active_stage = ""

            def emit_chunk(chunk: str) -> None:
                nonlocal active_stage
                streamed_response.append(chunk)
                if not on_event:
                    return
                partial_yaml = self._extract_streaming_yaml("".join(streamed_response))
                if not partial_yaml:
                    return
                next_stage = self._streaming_template_stage(partial_yaml)
                if next_stage != active_stage:
                    if active_stage:
                        on_event({"kind": "template_stage", "stage": active_stage, "status": "done"})
                    active_stage = next_stage
                    on_event({"kind": "template_stage", "stage": active_stage, "status": "running"})
                on_event({
                    "kind": "template_delta",
                    "stage": active_stage,
                    "content": chunk,
                    "templateYaml": partial_yaml,
                })

            response = await self.generate(
                prompt,
                max_tokens=4096,
                on_chunk=emit_chunk if on_event else None,
            )
            if active_stage and on_event:
                on_event({"kind": "template_stage", "stage": active_stage, "status": "done"})
            template_yaml = self._normalize_template_yaml(
                url,
                self._extract_yaml(response),
                analysis_result,
                page_title,
            )
            validation_error = self._template_validation_error(template_yaml)
            if not validation_error:
                break
            previous_template_yaml = template_yaml
        if validation_error:
            raise RuntimeError(f"Template validation failed: {validation_error}")
        if on_event:
            on_event({
                "kind": "template_delta",
                "content": "Template generated",
                "templateYaml": template_yaml,
            })
        return template_yaml

    @staticmethod
    def _safe_template_mapping(template_yaml: str) -> Dict[str, Any]:
        if not template_yaml.strip():
            return {}
        try:
            template = yaml.safe_load(template_yaml)
        except yaml.YAMLError:
            return {}
        return template if isinstance(template, dict) else {}

    @classmethod
    def _prompt_analysis_context(cls, analysis_result: Dict[str, Any]) -> Dict[str, Any]:
        context = dict(analysis_result)
        context["page_summary"] = str(context.get("page_summary") or "")[:300]
        context["page_structure"] = str(context.get("page_structure") or "")[:1600]
        context["api_endpoints"] = list(context.get("api_endpoints") or [])[:8]
        context["warnings"] = list(context.get("warnings") or [])[:5]

        compact_responses: list[Dict[str, Any]] = []
        for response in list(context.get("response_evidence") or [])[:3]:
            if not isinstance(response, dict):
                continue
            compact = dict(response)
            compact["url"] = str(compact.get("url") or "")[:500]
            compact["bodyPreview"] = str(compact.get("bodyPreview") or "")[:240]
            compact["recordFields"] = list(compact.get("recordFields") or [])[:20]
            sample_record = compact.get("sampleRecord") or {}
            compact["sampleRecord"] = (
                {
                    str(key): cls._compact_value(value)
                    for key, value in list(sample_record.items())[:12]
                }
                if isinstance(sample_record, dict)
                else {}
            )
            compact["links"] = [str(link)[:200] for link in list(compact.get("links") or [])[:3]]
            compact_responses.append(compact)
        context["response_evidence"] = compact_responses
        context["selected_candidate"] = compact_responses[0] if compact_responses else {}
        return context

    @staticmethod
    def _streaming_template_stage(template_yaml: str) -> str:
        keys = {
            match.group(1)
            for match in re.finditer(r"(?m)^([A-Za-z_][\w-]*)\s*:", template_yaml)
        }
        if keys & {"dedup_fields", "download"}:
            return "dedup_download"
        if keys & {"list_fields", "detail_page", "detail_request", "detail_fields"}:
            return "fields"
        if keys & {"response_type", "json_item_path", "json_total_path", "json_page_path"}:
            return "response"
        if keys & {"params", "batch_params", "list_page", "list_request", "list_pagination"}:
            return "request"
        return "site"

    @staticmethod
    def _extract_streaming_yaml(response: str) -> str:
        fence = re.search(
            r"```[ \t]*(?:yaml|yml)[ \t]*(?:\r?\n)?",
            response,
            re.IGNORECASE,
        )
        if fence:
            yaml_text = response[fence.end():]
        else:
            yaml_text = response
        template_start = re.search(r"(?m)^name\s*:", yaml_text)
        if not template_start:
            return ""
        yaml_text = yaml_text[template_start.start():]
        closing_fence = yaml_text.find("```")
        if closing_fence >= 0:
            yaml_text = yaml_text[:closing_fence]
        return yaml_text.rstrip()

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
        return self._extract_streaming_yaml(response).strip()

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
        data_type = re.sub(
            r"[^a-z0-9]+",
            "_",
            str(template.get("data_type") or "other").strip().lower(),
        ).strip("_") or "other"
        template["data_type"] = data_type
        if data_type == "game" or template.get("adapter"):
            template["adapter"] = template_name
        else:
            template["adapter"] = ""
        template.setdefault("anti_crawl_enabled", None)

        description = re.sub(r"\s+", " ", str(template.get("description") or "")).strip()
        if not description:
            description = re.sub(
                r"\s+",
                " ",
                str(analysis_result.get("page_summary") or page_title),
            ).strip()
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

    @staticmethod
    def merge_template_evidence(
        analysis_result: Dict[str, Any],
        template: Dict[str, Any],
    ) -> Dict[str, Any]:
        def integer(value: Any, default: int) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        result = dict(analysis_result)
        template_fields = [
            field
            for section in ("list_fields", "detail_fields")
            for field in (template.get(section) or [])
            if isinstance(field, dict) and field.get("name")
        ]
        pagination = template.get("list_pagination") or {}
        result.update({
            "data_type": str(template.get("data_type") or "other"),
            "selected_endpoint": str(template.get("list_page") or ""),
            "source_kind": "json" if template.get("response_type") == "json" else "html",
            "json_item_path": str(template.get("json_item_path") or ""),
            "fields": [
                {
                    "name": str(field.get("name") or ""),
                    "selector": str(field.get("selector") or ""),
                    "type": str(field.get("field_type") or "text"),
                    "sample_value": "",
                    "required": bool(field.get("required")),
                }
                for field in template_fields
            ],
            "pagination": {
                "type": str(pagination.get("type") or "none"),
                "list_page": str(template.get("list_page") or ""),
                "start_page": integer(pagination.get("start_page"), 1),
                "results_per_page": integer(pagination.get("results_per_page"), 0),
                "page_param": str(pagination.get("page_param") or "page"),
            },
            "dedup_fields": list(template.get("dedup_fields") or []),
            "adapter_requirements": [str(template.get("adapter"))] if template.get("adapter") else [],
        })
        return result

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
