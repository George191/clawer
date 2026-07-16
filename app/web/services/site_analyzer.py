from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import yaml
from lxml import etree, html as lxml_html

from app.config.settings import settings
from app.downloader.http_client import HttpClient
from app.models.template import RequestConfig

logger = logging.getLogger(__name__)

_HEADER_TAGS = {"h1", "h2", "h3", "h4"}
_ROOT_TAGS = {"article", "li", "div", "section"}
_URL_CLASS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")
_DATE_RE = re.compile(
    r"("
    r"\d{4}-\d{1,2}-\d{1,2}"
    r"|[A-Z][a-z]{2,9}\s+\d{1,2},\s+\d{4}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r")"
)
_NEXT_HINTS = ("next", "older", "more", "forward", "weiter", "suivant", "›", "»")
_BAD_HINTS = (
    "nav",
    "menu",
    "footer",
    "header",
    "breadcrumb",
    "pagination",
    "social",
    "share",
    "comment",
    "sidebar",
    "related",
    "promo",
    "ad",
)
_TITLE_HINTS = ("title", "headline", "entry-title", "post-title", "card-title", "heading")
_DATE_HINTS = ("date", "time", "publish", "posted", "timestamp", "meta")
_SUMMARY_HINTS = ("summary", "excerpt", "description", "dek", "teaser", "intro", "content")
_CONTENT_HINTS = ("content", "article", "entry", "post", "body", "story")
_IMAGE_ATTRS = ("src", "data-src", "data-lazy-src", "data-original")
_DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": settings.http_user_agent,
}
_MAX_PROBE_PAGE = 4096
_PROBE_PAGES = (2, 3, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 3000, 4096)
_PROMPT_FIELD_HINTS = {
    "title": ("标题", "名称", "名字", "title", "name"),
    "url": ("链接", "网址", "地址", "link", "url"),
    "date": ("日期", "时间", "发布时间", "date", "time", "published"),
    "summary": ("摘要", "简介", "描述", "正文", "内容", "summary", "description", "content"),
    "thumbnail": ("图片", "封面", "缩略图", "image", "thumbnail", "cover"),
}


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


class SiteAnalyzer:
    def __init__(self, http_client: HttpClient | None = None) -> None:
        self._client = http_client or HttpClient()

    async def close(self) -> None:
        await self._client.close()

    async def fetch_listing_page(self, url: str) -> str:
        return await self._client.request_page(url, self._default_request())

    async def analyze(self, url: str, prompt: str = "") -> AnalysisResult:
        html = await self.fetch_listing_page(url)
        return await self.analyze_html(url, html, prompt)

    async def analyze_html(self, url: str, html_text: str, prompt: str = "") -> AnalysisResult:
        barrier = self.detect_page_barrier(html_text)
        if barrier:
            raise ValueError(barrier)
        tree = lxml_html.fromstring(html_text, base_url=url)
        acquisition = await self._analyze_acquisition(url, html_text, tree)
        if acquisition.mode == "api":
            return self._build_api_result(url, prompt, acquisition)
        root_selector, root_nodes = self._detect_root_selector(tree)
        if not root_selector or not root_nodes:
            if acquisition.mode == "javascript":
                raise ValueError(
                    "The page is populated by JavaScript, but no verifiable JSON endpoint was found; "
                    "browser network capture is required"
                )
            raise ValueError("Unable to detect a repeated record container from the page")

        fields, sample_items = self._infer_fields(tree, root_selector, root_nodes, url)
        if not sample_items:
            raise ValueError("Unable to extract any sample records from the detected container")
        fields = self._apply_prompt_intent(fields, prompt)
        selected_names = {field.name for field in fields}
        sample_items = [
            {name: value for name, value in item.items() if name in selected_names}
            for item in sample_items
        ]

        detail_fields = await self._infer_detail_fields(sample_items)
        pagination = await self._detect_pagination(url, tree, root_selector, root_nodes)
        mode = self._choose_mode(fields)
        template_name = self._build_template_name(url)
        display_name = self._build_display_name(url)
        base_url = self._build_base_url(url)
        warnings = self._build_warnings(fields, pagination, detail_fields)
        template_dict = self._build_template_dict(
            template_name=template_name,
            display_name=display_name,
            base_url=base_url,
            list_page=pagination.list_page,
            fields=fields,
            detail_fields=detail_fields,
            pagination=pagination,
            mode=mode,
        )
        template_yaml = yaml.safe_dump(template_dict, allow_unicode=True, sort_keys=False)
        adapter_code = self._build_adapter_code(template_name, fields) if mode == "adapter" else ""

        return AnalysisResult(
            url=url,
            base_url=base_url,
            domain=urlparse(url).hostname or "",
            template_name=template_name,
            display_name=display_name,
            root_selector=root_selector,
            fields=fields,
            sample_items=sample_items,
            pagination=pagination,
            mode="generic_template" if mode == "generic" else "adapter",
            template_dict=template_dict,
            template_yaml=template_yaml,
            adapter_code=adapter_code,
            warnings=warnings,
            detail_fields=detail_fields,
            acquisition=acquisition,
        )

    @staticmethod
    def detect_page_barrier(html_text: str) -> str | None:
        lowered = html_text.lower()
        if "aliyunwaf_" in lowered or ('id="renderdata"' in lowered and "var arg1=" in lowered):
            return (
                "The site returned an Aliyun WAF challenge instead of page content; "
                "browser rendering with an available proxy is required"
            )
        if "cf-chl-" in lowered or "challenge-platform" in lowered:
            return (
                "The site returned a Cloudflare challenge instead of page content; "
                "browser rendering with an available proxy is required"
            )
        return None

    @staticmethod
    def _requested_field_names(prompt: str) -> set[str]:
        intent = prompt.casefold().strip()
        if not intent:
            return set()
        return {
            name
            for name, hints in _PROMPT_FIELD_HINTS.items()
            if any(hint.casefold() in intent for hint in hints)
        }

    @classmethod
    def _apply_prompt_intent(cls, fields: list[InferredField], prompt: str) -> list[InferredField]:
        requested = cls._requested_field_names(prompt)
        if not requested:
            return fields
        structural = {"title", "url"}
        return [field for field in fields if field.name in structural or field.name in requested]

    async def _analyze_acquisition(self, url: str, html_text: str, tree) -> AcquisitionAnalysis:
        script_text = "\n".join(tree.xpath("//script[not(@src)]/text()"))
        candidate_values: list[str] = []
        for pattern in (
            r"fetch\s*\(\s*['\"]([^'\"]+)['\"]",
            r"axios\.(?:get|post)\s*\(\s*['\"]([^'\"]+)['\"]",
            r"['\"](\/[^'\"\s]*(?:api|graphql)[^'\"\s]*)['\"]",
        ):
            candidate_values.extend(re.findall(pattern, script_text, flags=re.IGNORECASE))

        page_host = (urlparse(url).hostname or "").lower()
        candidates: list[str] = []
        for raw in candidate_values:
            candidate = urljoin(url, raw.replace("\\/", "/"))
            parsed = urlparse(candidate)
            if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() != page_host:
                continue
            if any(token in candidate for token in ("{", "}", "<", ">")):
                continue
            if candidate not in candidates:
                candidates.append(candidate)

        request = RequestConfig(
            method="GET",
            headers={**_DEFAULT_HEADERS, "Accept": "application/json, text/plain, */*", "Referer": url},
            encoding="utf-8",
        )
        for candidate in candidates[:5]:
            try:
                response_text = await self._client.request_page(candidate, request, no_timeout=True)
                payload = json.loads(response_text)
            except Exception as exc:
                logger.debug("Skip acquisition candidate %s: %s", candidate, exc)
                continue
            item_path, records = self._find_record_list(payload)
            if records:
                return AcquisitionAnalysis(
                    mode="api",
                    recommended_transport="json_api",
                    endpoint=candidate,
                    json_item_path=item_path,
                    evidence=["Verified JSON response", f"Detected {len(records)} sample records"],
                    candidates=candidates[:5],
                    sample_records=records[:20],
                )

        embedded_json = bool(
            tree.xpath("//script[@type='application/json' or @id='__NEXT_DATA__' or @id='__NUXT_DATA__']")
        )
        visible_text = self._normalize_text(tree.text_content())
        script_count = len(tree.xpath("//script"))
        if embedded_json:
            return AcquisitionAnalysis(
                mode="embedded_json",
                recommended_transport="html_with_hydration_state",
                evidence=["Detected framework hydration JSON in the HTML response"],
                candidates=candidates[:5],
            )
        if script_count >= 3 and len(visible_text) < 200:
            return AcquisitionAnalysis(
                mode="javascript",
                recommended_transport="browser_network_capture",
                evidence=[f"Only {len(visible_text)} visible characters with {script_count} scripts"],
                candidates=candidates[:5],
            )
        return AcquisitionAnalysis(
            mode="static_html",
            recommended_transport="html",
            evidence=["Repeated record content is present in the initial HTML response"],
            candidates=candidates[:5],
        )

    @classmethod
    def _find_record_list(
        cls,
        value: Any,
        path: str = "",
        depth: int = 0,
    ) -> tuple[str, list[dict[str, Any]]]:
        if depth > 6:
            return "", []
        if isinstance(value, list) and len(value) >= 2 and all(isinstance(item, dict) for item in value[:5]):
            return path, value
        if not isinstance(value, dict):
            return "", []
        best_path = ""
        best_records: list[dict[str, Any]] = []
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            found_path, records = cls._find_record_list(child, child_path, depth + 1)
            if len(records) > len(best_records):
                best_path, best_records = found_path, records
        return best_path, best_records

    def _build_api_result(
        self,
        url: str,
        prompt: str,
        acquisition: AcquisitionAnalysis,
    ) -> AnalysisResult:
        records = acquisition.sample_records
        scalar_keys = [
            str(key)
            for key, value in records[0].items()
            if value is None or isinstance(value, (str, int, float, bool))
        ][:20]
        requested_names = self._requested_field_names(prompt)
        if requested_names:
            matching = [
                key for key in scalar_keys
                if any(
                    hint.casefold() in key.casefold()
                    for name in requested_names
                    for hint in _PROMPT_FIELD_HINTS[name]
                )
            ]
            if matching:
                identity = [key for key in scalar_keys if key.casefold() in {"id", "url", "link", "title", "name"}]
                scalar_keys = list(dict.fromkeys(identity + matching))
        scalar_keys = scalar_keys[:12]
        if not scalar_keys:
            raise ValueError("The verified JSON endpoint did not expose scalar record fields")

        fields: list[InferredField] = []
        for index, key in enumerate(scalar_keys):
            sample = records[0].get(key)
            field_type = "boolean" if isinstance(sample, bool) else "number" if isinstance(sample, (int, float)) else "text"
            fields.append(InferredField(
                name=re.sub(r"[^a-zA-Z0-9_]+", "_", key).strip("_") or f"field_{index + 1}",
                field_type=field_type,
                relative_selector=key,
                sample=None if sample is None else str(sample)[:200],
                required=index == 0,
                global_selector=key,
                generic_supported=True,
            ))

        template_name = self._build_template_name(url)
        display_name = self._build_display_name(url)
        dedup_name = next((field.name for field in fields if field.name.casefold() in {"id", "url", "link"}), fields[0].name)
        template_dict: dict[str, Any] = {
            "name": template_name,
            "display_name": display_name,
            "base_url": self._build_base_url(url),
            "data_type": "other",
            "description": f"Auto-generated JSON API template for {display_name}",
            "response_type": "json",
            "json_item_path": acquisition.json_item_path,
            "list_page": acquisition.endpoint,
            "list_request": {
                "method": "GET",
                "headers": {**_DEFAULT_HEADERS, "Accept": "application/json, text/plain, */*", "Referer": url},
                "encoding": "utf-8",
            },
            "dedup_fields": [dedup_name],
            "list_fields": [
                {
                    "name": field.name,
                    "selector": field.relative_selector,
                    "selector_type": "json",
                    "field_type": field.field_type,
                    "required": field.required,
                }
                for field in fields
            ],
        }
        pagination = PaginationAnalysis(
            type="page_number",
            list_page=acquisition.endpoint,
            start_page=1,
            results_per_page=len(records),
            verified_pages=1,
            max_pages=1,
        )
        return AnalysisResult(
            url=url,
            base_url=self._build_base_url(url),
            domain=urlparse(url).hostname or "",
            template_name=template_name,
            display_name=display_name,
            root_selector=acquisition.json_item_path,
            fields=fields,
            sample_items=records[:20],
            pagination=pagination,
            mode="generic_template",
            template_dict=template_dict,
            template_yaml=yaml.safe_dump(template_dict, allow_unicode=True, sort_keys=False),
            adapter_code="",
            warnings=["Verified JSON API selected instead of DOM parsing", "API pagination was not inferred"],
            detail_fields=[],
            acquisition=acquisition,
        )

    @staticmethod
    def _default_request() -> RequestConfig:
        return RequestConfig(method="GET", headers=dict(_DEFAULT_HEADERS), encoding="utf-8")

    @staticmethod
    def _normalize_text(value: str | None) -> str:
        if not value:
            return ""
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _build_base_url(url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"

    @staticmethod
    def _build_template_name(url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.hostname or "site").lower()
        if host.startswith("www."):
            host = host[4:]
        parts = [part for part in host.split(".") if part]
        path_parts = [part for part in parsed.path.split("/") if part][:2]
        raw = "_".join(parts[:-1] if len(parts) > 1 else parts)
        if not raw:
            raw = parts[0] if parts else "site"
        slug_parts = [raw]
        slug_parts.extend(re.sub(r"[^a-z0-9]+", "_", part.lower()).strip("_") for part in path_parts)
        return "_".join(part for part in slug_parts if part)

    @staticmethod
    def _build_display_name(url: str) -> str:
        parsed = urlparse(url)
        host = (parsed.hostname or "site").lower()
        if host.startswith("www."):
            host = host[4:]
        title = host.replace(".", " ").title()
        path = " / ".join(part for part in parsed.path.split("/") if part)
        return f"{title} {path}".strip() if path else title

    @staticmethod
    def _valid_css_name(value: str | None) -> bool:
        return bool(value and _URL_CLASS_RE.match(value))

    @classmethod
    def _element_classes(cls, element) -> list[str]:
        class_attr = element.get("class", "")
        classes: list[str] = []
        for raw in class_attr.split():
            if not cls._valid_css_name(raw):
                continue
            lower = raw.lower()
            if any(hint in lower for hint in ("active", "current", "selected", "loaded", "lazy")):
                continue
            classes.append(raw)
        return classes[:3]

    @classmethod
    def _node_token_variants(cls, element) -> list[str]:
        tag = getattr(element, "tag", None)
        if not isinstance(tag, str):
            return []

        variants: list[str] = []
        element_id = element.get("id")
        if cls._valid_css_name(element_id):
            variants.append(f"{tag}#{element_id}")

        classes = cls._element_classes(element)
        if classes:
            joined = ".".join(classes)
            variants.append(f"{tag}.{joined}")
            variants.extend(f"{tag}.{class_name}" for class_name in classes)

        variants.append(tag)
        deduped: list[str] = []
        seen: set[str] = set()
        for variant in variants:
            if variant not in seen:
                deduped.append(variant)
                seen.add(variant)
        return deduped

    @classmethod
    def _node_text_score(cls, node) -> float:
        text = cls._normalize_text(node.text_content())
        if not text:
            return 0.0
        return min(len(text), 300)

    @classmethod
    def _contains_bad_hint(cls, node) -> bool:
        haystack = " ".join(
            [
                getattr(node, "tag", "") or "",
                node.get("id", "") or "",
                node.get("class", "") or "",
            ]
        ).lower()
        return any(hint in haystack for hint in _BAD_HINTS)

    @classmethod
    def _child_signature(cls, node) -> tuple[str, tuple[str, ...]]:
        tag = getattr(node, "tag", "") or ""
        classes = tuple(cls._element_classes(node))
        return tag, classes

    def _detect_root_selector(self, tree) -> tuple[str | None, list[Any]]:
        best_score = float("-inf")
        best_selector: str | None = None
        best_nodes: list[Any] = []

        for parent in tree.iter():
            tag = getattr(parent, "tag", None)
            if not isinstance(tag, str):
                continue
            children = [child for child in parent if isinstance(getattr(child, "tag", None), str)]
            if len(children) < 4:
                continue

            grouped: dict[tuple[str, tuple[str, ...]], list[Any]] = {}
            for child in children:
                signature = self._child_signature(child)
                grouped.setdefault(signature, []).append(child)

            for nodes in grouped.values():
                if len(nodes) < 4:
                    continue
                score = self._score_root_group(parent, nodes)
                if score <= best_score:
                    continue
                selector = self._build_group_selector(tree, parent, nodes)
                if not selector:
                    continue
                best_score = score
                best_selector = selector
                best_nodes = nodes

        if best_selector:
            return best_selector, best_nodes

        fallback_selector, fallback_nodes = self._fallback_root_selector(tree)
        return fallback_selector, fallback_nodes

    def _fallback_root_selector(self, tree) -> tuple[str | None, list[Any]]:
        best_score = float("-inf")
        best_selector: str | None = None
        best_nodes: list[Any] = []

        for tag in _ROOT_TAGS:
            for node in tree.cssselect(tag):
                parent = node.getparent()
                if parent is None:
                    continue
                siblings = [child for child in parent if getattr(child, "tag", None) == tag]
                if len(siblings) < 4:
                    continue
                score = self._score_root_group(parent, siblings)
                if score <= best_score:
                    continue
                selector = self._build_group_selector(tree, parent, siblings)
                if not selector:
                    continue
                best_score = score
                best_selector = selector
                best_nodes = siblings
        return best_selector, best_nodes

    def _score_root_group(self, parent, nodes: list[Any]) -> float:
        anchor_ratio = sum(1 for node in nodes if node.cssselect("a[href]")) / len(nodes)
        time_ratio = sum(1 for node in nodes if node.cssselect("time")) / len(nodes)
        image_ratio = sum(1 for node in nodes if node.cssselect("img")) / len(nodes)
        avg_text = sum(self._node_text_score(node) for node in nodes) / len(nodes)
        bonus = 5 if any(self._element_classes(node) for node in nodes) else 0
        penalty = 30 if self._contains_bad_hint(parent) or any(self._contains_bad_hint(node) for node in nodes) else 0
        return len(nodes) * 5 + anchor_ratio * 25 + time_ratio * 8 + image_ratio * 5 + avg_text / 20 + bonus - penalty

    def _build_group_selector(self, tree, parent, nodes: list[Any]) -> str | None:
        candidate_nodes = set(nodes)
        parent_candidates = [""] + self._absolute_selector_candidates(parent)
        child_candidates = self._node_token_variants(nodes[0])

        for parent_selector in parent_candidates:
            for child_selector in child_candidates:
                selectors = []
                if parent_selector:
                    selectors.append(f"{parent_selector} > {child_selector}")
                    selectors.append(f"{parent_selector} {child_selector}")
                else:
                    selectors.append(child_selector)

                for selector in selectors:
                    try:
                        matches = tree.cssselect(selector)
                    except Exception:
                        continue
                    if len(matches) != len(nodes):
                        continue
                    if set(matches) != candidate_nodes:
                        continue
                    return selector
        return None

    def _absolute_selector_candidates(self, node) -> list[str]:
        path: list[Any] = []
        current = node
        while current is not None and len(path) < 4:
            tag = getattr(current, "tag", None)
            if not isinstance(tag, str) or tag == "html":
                break
            path.append(current)
            current = current.getparent()

        candidates: list[str] = []
        for depth in range(1, len(path) + 1):
            chain = list(reversed(path[:depth]))
            token_options = [self._node_token_variants(part)[:2] for part in chain]
            if any(not options for options in token_options):
                continue
            for tokens in self._product(token_options):
                candidates.append(" > ".join(tokens))
                candidates.append(" ".join(tokens))

        deduped: list[str] = []
        seen: set[str] = set()
        for candidate in sorted(candidates, key=len):
            if candidate not in seen:
                deduped.append(candidate)
                seen.add(candidate)
        return deduped

    @staticmethod
    def _product(options: list[list[str]]) -> list[tuple[str, ...]]:
        results: list[tuple[str, ...]] = [tuple()]
        for option_group in options:
            next_results: list[tuple[str, ...]] = []
            for prefix in results:
                for option in option_group:
                    next_results.append(prefix + (option,))
            results = next_results
        return results

    def _infer_fields(
        self,
        tree,
        root_selector: str,
        root_nodes: list[Any],
        page_url: str,
    ) -> tuple[list[InferredField], list[dict[str, Any]]]:
        title_targets = [self._pick_title_node(node) for node in root_nodes]
        url_targets = list(title_targets)
        date_targets = [self._pick_date_node(node) for node in root_nodes]
        summary_targets = [self._pick_summary_node(node, title_targets[index]) for index, node in enumerate(root_nodes)]
        image_targets = [self._pick_image_node(node) for node in root_nodes]

        fields: list[InferredField] = []
        fields.append(self._build_field("title", "text", root_selector, root_nodes, title_targets, required=True))
        fields.append(self._build_field("url", "href", root_selector, root_nodes, url_targets, required=True))
        date_field = self._build_field("date", "text", root_selector, root_nodes, date_targets)
        summary_field = self._build_field("summary", "text", root_selector, root_nodes, summary_targets)
        image_field = self._build_image_field(root_selector, root_nodes, image_targets)
        for candidate in (date_field, summary_field, image_field):
            if candidate.relative_selector:
                fields.append(candidate)

        usable_fields = [field for field in fields if field.relative_selector]
        sample_items = self._extract_sample_items(root_nodes, usable_fields, page_url)
        return usable_fields, sample_items

    def _build_field(
        self,
        name: str,
        field_type: str,
        root_selector: str,
        root_nodes: list[Any],
        targets: list[Any | None],
        required: bool = False,
        attr_name: str | None = None,
    ) -> InferredField:
        relative_selector = self._infer_relative_selector(root_nodes, targets)
        sample = self._sample_from_targets(targets, field_type, attr_name)
        if not relative_selector:
            return InferredField(
                name=name,
                field_type=field_type,
                relative_selector=None,
                sample=sample,
                required=required,
                attr_name=attr_name,
            )

        generic_supported = all(target is not None for target in targets)
        global_selector = f"{root_selector} {relative_selector}"
        if generic_supported:
            try:
                generic_supported = len(root_nodes) == len(root_nodes[0].getroottree().cssselect(global_selector))
            except Exception:
                generic_supported = False

        return InferredField(
            name=name,
            field_type=field_type,
            relative_selector=relative_selector,
            sample=sample,
            required=required,
            attr_name=attr_name,
            global_selector=global_selector,
            generic_supported=generic_supported,
            adapter_supported=True,
        )

    def _build_image_field(
        self,
        root_selector: str,
        root_nodes: list[Any],
        targets: list[tuple[Any, str] | None],
    ) -> InferredField:
        image_nodes = [target[0] if target else None for target in targets]
        attr_name = next((target[1] for target in targets if target), None)
        field_type = "src" if attr_name == "src" else "attr"
        return self._build_field(
            name="thumbnail",
            field_type=field_type,
            root_selector=root_selector,
            root_nodes=root_nodes,
            targets=image_nodes,
            required=False,
            attr_name=None if attr_name == "src" else attr_name,
        )

    def _extract_sample_items(
        self,
        root_nodes: list[Any],
        fields: list[InferredField],
        page_url: str,
    ) -> list[dict[str, Any]]:
        sample_items: list[dict[str, Any]] = []
        for root in root_nodes[:20]:
            item: dict[str, Any] = {}
            for field in fields:
                if not field.relative_selector:
                    continue
                value = self._extract_from_root(root, field)
                if value is None or value == "":
                    continue
                if field.name in {"url", "thumbnail"}:
                    value = urljoin(page_url, value)
                item[field.name] = value
            if item.get("title") and item.get("url"):
                sample_items.append(item)
        return sample_items

    def _extract_from_root(self, root, field: InferredField) -> str | None:
        if not field.relative_selector:
            return None
        matches = root.cssselect(field.relative_selector)
        if not matches:
            return None
        target = matches[0]
        return self._read_element_value(target, field.field_type, field.attr_name)

    def _sample_from_targets(
        self,
        targets: list[Any | None],
        field_type: str,
        attr_name: str | None,
    ) -> str | None:
        for target in targets:
            if target is None:
                continue
            value = self._read_element_value(target, field_type, attr_name)
            if value:
                return self._normalize_text(value)[:200]
        return None

    def _infer_relative_selector(self, root_nodes: list[Any], targets: list[Any | None]) -> str | None:
        indexed_targets = [(index, target) for index, target in enumerate(targets) if target is not None]
        if not indexed_targets:
            return None

        sample_index, sample_target = indexed_targets[0]
        sample_root = root_nodes[sample_index]
        candidates = self._relative_selector_candidates(sample_root, sample_target)
        if not candidates:
            return None

        for candidate in sorted(candidates, key=lambda value: (len(value), value.count(">"))):
            valid = True
            for root, target in zip(root_nodes, targets):
                try:
                    matches = root.cssselect(candidate)
                except Exception:
                    valid = False
                    break
                if target is None:
                    if matches:
                        valid = False
                        break
                    continue
                if len(matches) != 1 or matches[0] is not target:
                    valid = False
                    break
            if valid:
                return candidate
        return None

    def _relative_selector_candidates(self, root, target) -> list[str]:
        path: list[Any] = []
        current = target
        while current is not None and current is not root:
            path.append(current)
            current = current.getparent()
        if current is not root:
            return []

        candidates: list[str] = []
        max_depth = min(len(path), 4)
        for depth in range(1, max_depth + 1):
            chain = list(reversed(path[:depth]))
            token_options = [self._node_token_variants(node)[:2] for node in chain]
            if any(not options for options in token_options):
                continue
            for tokens in self._product(token_options):
                candidates.append(" > ".join(tokens))
                candidates.append(" ".join(tokens))

        deduped: list[str] = []
        seen: set[str] = set()
        for candidate in sorted(candidates, key=len):
            if candidate not in seen:
                deduped.append(candidate)
                seen.add(candidate)
        return deduped

    def _pick_title_node(self, root):
        best_node = None
        best_score = float("-inf")
        for node in root.cssselect("a[href]"):
            href = node.get("href", "")
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            text = self._normalize_text(node.text_content())
            if len(text) < 6:
                continue
            score = len(text)
            if len(text) > 180:
                score -= 60
            parent = node.getparent()
            if getattr(node, "tag", "") in _HEADER_TAGS or getattr(parent, "tag", "") in _HEADER_TAGS:
                score += 120
            haystack = " ".join(
                [
                    node.get("class", "") or "",
                    node.get("id", "") or "",
                    parent.get("class", "") if parent is not None else "",
                    parent.get("id", "") if parent is not None else "",
                ]
            ).lower()
            if any(hint in haystack for hint in _TITLE_HINTS):
                score += 80
            if "/tag/" in href or "/category/" in href:
                score -= 80
            if node.xpath("ancestor::nav"):
                score -= 100
            if score > best_score:
                best_score = score
                best_node = node
        return best_node

    def _pick_date_node(self, root):
        for selector in ("time[datetime]", "time"):
            matches = root.cssselect(selector)
            if matches:
                return matches[0]

        best_node = None
        best_score = float("-inf")
        for node in root.xpath(".//*[self::span or self::div or self::p or self::a]"):
            text = self._normalize_text(node.text_content())
            haystack = " ".join([node.get("class", "") or "", node.get("id", "") or "", text]).lower()
            if not (_DATE_RE.search(text) or any(hint in haystack for hint in _DATE_HINTS)):
                continue
            score = 50 - len(text)
            if any(hint in haystack for hint in _DATE_HINTS):
                score += 40
            if _DATE_RE.search(text):
                score += 30
            if score > best_score:
                best_score = score
                best_node = node
        return best_node

    def _pick_summary_node(self, root, title_node):
        title_text = self._normalize_text(title_node.text_content()) if title_node is not None else ""
        best_node = None
        best_score = float("-inf")
        for node in root.xpath(".//*[self::p or self::div or self::span]"):
            text = self._normalize_text(node.text_content())
            if len(text) < 30 or len(text) > 500:
                continue
            if title_text and text == title_text:
                continue
            if title_text and title_text in text and len(text) <= len(title_text) + 10:
                continue
            score = min(len(text), 240)
            haystack = " ".join([node.get("class", "") or "", node.get("id", "") or ""]).lower()
            if any(hint in haystack for hint in _SUMMARY_HINTS):
                score += 80
            if node.xpath("ancestor::nav"):
                score -= 100
            if score > best_score:
                best_score = score
                best_node = node
        return best_node

    def _pick_image_node(self, root) -> tuple[Any, str] | None:
        for node in root.cssselect("img"):
            for attr_name in _IMAGE_ATTRS:
                value = node.get(attr_name)
                if value:
                    return node, attr_name
        return None

    @classmethod
    def _read_element_value(cls, element, field_type: str, attr_name: str | None) -> str | None:
        if field_type == "text":
            return cls._normalize_text(element.text_content())
        if field_type == "href":
            return cls._normalize_text(element.get("href", ""))
        if field_type == "src":
            return cls._normalize_text(element.get("src", ""))
        if field_type == "attr" and attr_name:
            return cls._normalize_text(element.get(attr_name, ""))
        if field_type == "html":
            return etree.tostring(element, encoding="unicode", method="html")
        return None

    async def _infer_detail_fields(self, sample_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        detail_url = next((item.get("url") for item in sample_items if item.get("url")), None)
        if not detail_url:
            return []

        try:
            html_text = await self._client.request_page(detail_url, self._default_request())
        except Exception as exc:
            logger.debug("Skip detail inference for %s: %s", detail_url, exc)
            return []

        tree = lxml_html.fromstring(html_text, base_url=detail_url)
        best_node = None
        best_score = float("-inf")
        for node in tree.xpath(".//*[self::article or self::main or self::section or self::div]"):
            text = self._normalize_text(node.text_content())
            if len(text) < 400:
                continue
            paragraphs = node.cssselect("p")
            if len(paragraphs) < 3:
                continue
            haystack = " ".join([node.get("class", "") or "", node.get("id", "") or ""]).lower()
            score = len(paragraphs) * 15 + min(len(text), 4000) / 100
            if any(hint in haystack for hint in _CONTENT_HINTS):
                score += 80
            if any(hint in haystack for hint in _BAD_HINTS):
                score -= 120
            if score > best_score:
                best_score = score
                best_node = node

        if best_node is None:
            return []

        selector = self._absolute_selector_for_node(tree, best_node)
        if not selector:
            return []

        return [{
            "name": "content",
            "selector": selector,
            "selector_type": "css",
            "field_type": "html",
            "required": False,
        }]

    def _absolute_selector_for_node(self, tree, node) -> str | None:
        for candidate in self._absolute_selector_candidates(node):
            try:
                matches = tree.cssselect(candidate)
            except Exception:
                continue
            if len(matches) == 1 and matches[0] is node:
                return candidate
        return None

    async def _detect_pagination(self, url: str, tree, root_selector: str, root_nodes: list[Any]) -> PaginationAnalysis:
        next_link = self._pick_next_link(tree, url)
        current_count = len(root_nodes)
        fallback = PaginationAnalysis(
            type="page_number",
            list_page=self._current_list_page(url),
            start_page=1,
            results_per_page=current_count,
            verified_pages=1,
            max_pages=1,
        )
        if not next_link:
            return fallback

        list_page, start_page, page_param = self._derive_list_page(url, next_link.get("href", ""))
        if not list_page:
            return fallback

        next_selector = self._absolute_selector_for_node(tree, next_link)
        page_signatures: dict[int, str] = {start_page: self._page_signature(root_nodes)}
        probes: list[dict[str, Any]] = [{"page": start_page, "status": "ok", "count": current_count}]
        verified_pages = start_page
        exact_last_page: int | None = None

        next_page_number = start_page + 1
        first_probe = await self._probe_page(url, list_page, next_page_number, root_selector)
        probes.append(first_probe)
        if first_probe["status"] != "ok":
            return PaginationAnalysis(
                type="next_page" if next_selector else "page_number",
                list_page=list_page,
                start_page=start_page,
                results_per_page=current_count,
                next_selector=next_selector,
                page_param=page_param,
                verified_pages=start_page,
                max_pages=start_page,
                exact_last_page=start_page,
                probes=probes,
            )

        verified_pages = next_page_number
        page_signatures[next_page_number] = first_probe["signature"]
        if not first_probe.get("has_next") and next_selector:
            exact_last_page = next_page_number

        last_success = next_page_number
        first_failure: int | None = None
        for page_number in _PROBE_PAGES:
            if page_number <= next_page_number:
                continue
            if page_number > _MAX_PROBE_PAGE:
                break
            probe = await self._probe_page(url, list_page, page_number, root_selector)
            probes.append(probe)
            if probe["status"] != "ok":
                first_failure = page_number
                break
            signature = probe["signature"]
            if signature in page_signatures.values():
                probe["status"] = "duplicate"
                first_failure = page_number
                break
            page_signatures[page_number] = signature
            verified_pages = max(verified_pages, page_number)
            last_success = page_number
            if not probe.get("has_next") and next_selector:
                exact_last_page = page_number
                break

        if exact_last_page is None and next_selector and first_failure is not None and last_success + 1 < first_failure:
            exact_last_page = await self._binary_search_last_page(url, list_page, root_selector, last_success, first_failure, probes)

        max_pages = exact_last_page if exact_last_page is not None else 0
        pagination_type = "next_page" if next_selector else "page_number"
        return PaginationAnalysis(
            type=pagination_type,
            list_page=list_page,
            start_page=start_page,
            results_per_page=current_count,
            next_selector=next_selector,
            page_param=page_param,
            verified_pages=verified_pages,
            max_pages=max_pages,
            exact_last_page=exact_last_page,
            probes=probes,
        )

    @staticmethod
    def _current_list_page(url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path or "/"
        if parsed.query:
            return f"{path}?{parsed.query}"
        return path

    def _pick_next_link(self, tree, current_url: str):
        current_host = urlparse(current_url).hostname
        candidates = []
        for node in tree.cssselect("a[href]"):
            href = node.get("href", "")
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue
            absolute = urljoin(current_url, href)
            parsed = urlparse(absolute)
            if parsed.hostname != current_host:
                continue
            text = self._normalize_text(node.text_content()).lower()
            haystack = " ".join(
                [
                    text,
                    node.get("rel", "") or "",
                    node.get("class", "") or "",
                    node.get("id", "") or "",
                    href,
                ]
            ).lower()
            score = 0
            if node.get("rel", "").lower() == "next":
                score += 200
            if any(hint in haystack for hint in _NEXT_HINTS):
                score += 120
            if text.isdigit():
                score += 10
            if "page" in href.lower():
                score += 20
            if score > 0:
                candidates.append((score, node))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    def _derive_list_page(self, current_url: str, next_href: str) -> tuple[str | None, int, str | None]:
        current = urlparse(current_url)
        next_url = urlparse(urljoin(current_url, next_href))

        current_query = dict(parse_qsl(current.query, keep_blank_values=True))
        next_query = dict(parse_qsl(next_url.query, keep_blank_values=True))
        for key, next_value in next_query.items():
            if not next_value.isdigit():
                continue
            current_value = current_query.get(key)
            if current_value and current_value.isdigit():
                current_page = int(current_value)
            else:
                current_page = int(next_value) - 1
            if int(next_value) != current_page + 1:
                continue
            current_query[key] = "{page}"
            query = urlencode(current_query)
            list_page = current.path or "/"
            if query:
                list_page = f"{list_page}?{query}"
            return list_page, max(current_page, 1), key

        next_path = next_url.path or "/"
        patterns = [
            re.compile(r"^(?P<prefix>.*?/page/)(?P<page>\d+)(?P<suffix>/?)$"),
            re.compile(r"^(?P<prefix>.*?/p/)(?P<page>\d+)(?P<suffix>/?)$"),
            re.compile(r"^(?P<prefix>.*?)(?P<page>\d+)(?P<suffix>/?)$"),
        ]
        for pattern in patterns:
            match = pattern.match(next_path)
            if not match:
                continue
            page_value = int(match.group("page"))
            prefix = match.group("prefix")
            suffix = match.group("suffix")
            list_path = f"{prefix}{{page}}{suffix}"
            return list_path, max(page_value - 1, 1), None

        return None, 1, None

    async def _probe_page(self, current_url: str, list_page: str, page_number: int, root_selector: str) -> dict[str, Any]:
        probe_url = self._build_probe_url(current_url, list_page, page_number)
        try:
            html_text = await self._client.request_page(probe_url, self._default_request())
            tree = lxml_html.fromstring(html_text, base_url=probe_url)
            nodes = tree.cssselect(root_selector)
            if not nodes:
                return {"page": page_number, "status": "empty", "count": 0, "url": probe_url}
            signature = self._page_signature(nodes)
            next_link = self._pick_next_link(tree, probe_url)
            return {
                "page": page_number,
                "status": "ok",
                "count": len(nodes),
                "url": probe_url,
                "signature": signature,
                "has_next": next_link is not None,
            }
        except Exception as exc:
            return {
                "page": page_number,
                "status": "error",
                "url": probe_url,
                "error": str(exc),
            }

    async def _binary_search_last_page(
        self,
        current_url: str,
        list_page: str,
        root_selector: str,
        low: int,
        high: int,
        probes: list[dict[str, Any]],
    ) -> int:
        last_success = low
        failure = high
        while last_success + 1 < failure:
            mid = (last_success + failure) // 2
            probe = await self._probe_page(current_url, list_page, mid, root_selector)
            probes.append(probe)
            if probe["status"] == "ok":
                last_success = mid
            else:
                failure = mid
        return last_success

    @staticmethod
    def _build_probe_url(current_url: str, list_page: str, page_number: int) -> str:
        rendered = list_page.replace("{page}", str(page_number))
        parsed = urlparse(current_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        return urljoin(base, rendered)

    def _page_signature(self, nodes: list[Any]) -> str:
        parts = [self._normalize_text(node.text_content())[:120] for node in nodes[:5]]
        return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _choose_mode(fields: list[InferredField]) -> str:
        required_generic = {field.name for field in fields if field.required and field.generic_supported}
        if {"title", "url"} != required_generic:
            return "adapter"
        adapter_only_fields = [
            field
            for field in fields
            if field.name not in {"title", "url"}
            and field.adapter_supported
            and not field.generic_supported
        ]
        return "adapter" if adapter_only_fields else "generic"

    def _build_warnings(
        self,
        fields: list[InferredField],
        pagination: PaginationAnalysis,
        detail_fields: list[dict[str, Any]],
    ) -> list[str]:
        warnings: list[str] = []
        missing_required = [field.name for field in fields if field.required and not field.relative_selector]
        if missing_required:
            warnings.append(f"Missing required fields: {', '.join(missing_required)}")
        adapter_only = [
            field.name
            for field in fields
            if field.adapter_supported and not field.generic_supported and field.name not in {"title", "url"}
        ]
        if adapter_only:
            warnings.append(
                "Optional fields require item-level adapter parsing: " + ", ".join(adapter_only)
            )
        if pagination.verified_pages < 2:
            warnings.append("Pagination was not verified beyond the entry page")
        if not detail_fields:
            warnings.append("Detail content selector was not inferred")
        return warnings

    def _build_template_dict(
        self,
        template_name: str,
        display_name: str,
        base_url: str,
        list_page: str,
        fields: list[InferredField],
        detail_fields: list[dict[str, Any]],
        pagination: PaginationAnalysis,
        mode: str,
    ) -> dict[str, Any]:
        list_fields: list[dict[str, Any]]
        adapter_name: str | None = None

        if mode == "generic":
            list_fields = [
                field.template_dict(field.global_selector or "")
                for field in fields
                if field.generic_supported and field.global_selector
            ]
        else:
            adapter_name = template_name
            list_fields = [{
                "name": "item_html",
                "selector": fields[0].global_selector.rsplit(" ", 1)[0] if fields and fields[0].global_selector else "",
                "selector_type": "css",
                "field_type": "html",
                "required": True,
            }]

        payload: dict[str, Any] = {
            "name": template_name,
            "display_name": display_name,
            "base_url": base_url,
            "data_type": "news" if any(field.name == "date" for field in fields) else "other",
            "description": f"Auto-generated template for {display_name}",
            "response_type": "html",
            "list_page": list_page,
            "list_request": {
                "method": "GET",
                "headers": dict(_DEFAULT_HEADERS),
                "encoding": "utf-8",
            },
            "dedup_fields": ["url"],
            "list_fields": list_fields,
        }
        if adapter_name:
            payload["adapter"] = adapter_name
        if pagination.max_pages != 1 or pagination.verified_pages > 1 or pagination.next_selector:
            payload["list_pagination"] = {
                "type": pagination.type,
                "start_page": pagination.start_page,
                "max_pages": pagination.max_pages,
                "results_per_page": pagination.results_per_page,
            }
            if pagination.next_selector:
                payload["list_pagination"]["next_selector"] = pagination.next_selector
            if pagination.page_param:
                payload["list_pagination"]["page_param"] = pagination.page_param
        if detail_fields:
            payload["detail_page"] = "{url}"
            payload["detail_request"] = {
                "method": "GET",
                "headers": dict(_DEFAULT_HEADERS),
                "encoding": "utf-8",
            }
            payload["detail_fields"] = detail_fields
        return payload

    def _build_adapter_code(self, template_name: str, fields: list[InferredField]) -> str:
        ordered_fields = []
        for name in ("title", "url", "date", "summary", "thumbnail"):
            field = next((candidate for candidate in fields if candidate.name == name and candidate.adapter_supported), None)
            if field:
                ordered_fields.append(field)

        lines = [
            "from __future__ import annotations",
            "",
            "from typing import Any",
            "from urllib.parse import urljoin",
            "",
            "from lxml import html as lxml_html",
            "",
            "from app.adapters import BaseSiteAdapter, register_adapter",
            "",
            f"@register_adapter(\"{template_name}\")",
            f"class {self._camel_name(template_name)}Adapter(BaseSiteAdapter):",
            f"    adapter_name = \"{template_name}\"",
            "",
            "    async def on_after_page(self, page: int, records: list[dict]) -> list[dict]:",
            "        parsed: list[dict] = []",
            "        for record in records:",
            "            item_html = record.get(\"item_html\")",
            "            if not item_html:",
            "                continue",
            "            root = lxml_html.fromstring(item_html)",
            "            item: dict[str, Any] = {}",
        ]

        for field in ordered_fields:
            if not field.relative_selector:
                continue
            if field.name == "title":
                lines.extend([
                    f"            title = self._extract_text(root, {field.relative_selector!r})",
                    "            if not title:",
                    "                continue",
                    "            item[\"title\"] = title",
                ])
                continue
            if field.name == "url":
                attr_name = field.attr_name or "href"
                lines.extend([
                    f"            raw_url = self._extract_attr(root, {field.relative_selector!r}, {attr_name!r})",
                    "            if not raw_url:",
                    "                continue",
                    "            item[\"url\"] = urljoin(self._base_url + \"/\", raw_url)",
                ])
                continue
            if field.field_type == "text":
                lines.extend([
                    f"            {field.name} = self._extract_text(root, {field.relative_selector!r})",
                    f"            if {field.name}:",
                    f"                item[{field.name!r}] = {field.name}",
                ])
            elif field.field_type == "src":
                lines.extend([
                    f"            {field.name} = self._extract_attr(root, {field.relative_selector!r}, \"src\")",
                    f"            if {field.name}:",
                    f"                item[{field.name!r}] = urljoin(self._base_url + \"/\", {field.name})",
                ])
            elif field.field_type == "attr" and field.attr_name:
                lines.extend([
                    f"            {field.name} = self._extract_attr(root, {field.relative_selector!r}, {field.attr_name!r})",
                    f"            if {field.name}:",
                    f"                item[{field.name!r}] = urljoin(self._base_url + \"/\", {field.name})",
                ])

        lines.extend([
            "            parsed.append(item)",
            "        return parsed",
            "",
            "    @staticmethod",
            "    def _first(root, selector: str):",
            "        matches = root.cssselect(selector)",
            "        return matches[0] if matches else None",
            "",
            "    @classmethod",
            "    def _extract_text(cls, root, selector: str) -> str | None:",
            "        node = cls._first(root, selector)",
            "        if node is None:",
            "            return None",
            "        text = \" \".join(node.text_content().split())",
            "        return text or None",
            "",
            "    @classmethod",
            "    def _extract_attr(cls, root, selector: str, attr_name: str) -> str | None:",
            "        node = cls._first(root, selector)",
            "        if node is None:",
            "            return None",
            "        value = (node.get(attr_name) or \"\").strip()",
            "        return value or None",
        ])
        return "\n".join(lines) + "\n"

    @staticmethod
    def _camel_name(value: str) -> str:
        return "".join(part.capitalize() for part in value.split("_"))
