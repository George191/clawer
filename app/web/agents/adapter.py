from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.logger import get_logger
from app.web.agents.base import BaseAgent
from app.web.services.ai_collect_store import ai_collect_store

logger = get_logger(__name__)


@dataclass
class AdapterResult:
    adapter_code: str
    warnings: list[str] = None
    adapter_name: str = ""

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


class AdapterAgent(BaseAgent):
    def __init__(self):
        model_path = (
            Path(__file__).parents[3] / "models" / "Qwen" / "2.5-Coder-1.5B-Instruct"
        )
        super().__init__(str(model_path))
        self._register_default_prompts()

    def _register_default_prompts(self) -> None:
        self.register_prompt(
            "generate_adapter",
            """
                Write the complete Python adapter required by the confirmed YAML. Return no analysis.

                Decision order:
                1. YAML is the contract. Emit exactly its business fields and required adapter-produced resources; do not restore omitted IDs, aliases or metadata.
                2. Infer only reusable architecture from same-data-type adapter summaries: base class, necessary hooks, parameter flow and resource handling. Never copy another site's URL, selector, constant or field. Preserve existing code unless the user request or YAML requires a change.
                3. Add code only for behavior the generic template engine cannot perform.

                Implementation rules:
                - Import BaseSiteAdapter/register_adapter from app.adapters and HttpClient from app.downloader.http_client; use the injected HttpClient and project logging, and reuse NewsBaseAdapter helpers for news when applicable. Add no client, dependency, standalone crawler or unrelated framework code.
                - Implement only required hooks: parse_list_response, on_before_crawl, on_after_page, on_error, on_request_headers, on_page_advance or build_batch_param_value. Leave ordinary selectors/parsing to YAML; for an explicitly required mixed-format hook, reuse TemplateParser with YAML list_fields.
                - Match response_type exactly. JSON uses engine-decoded data, json_item_path and list_fields; do not sniff content or switch to HTML/text. Parse HTML/text only when declared. Add a fallback only when the YAML evidence explicitly requires it.
                - Preserve source business values. Do not synthesize, concatenate, normalize, reinterpret or implement multi-key alias fallbacks. Preserve required params without defaults; never invent pagination, loops or page parameters. Treat dedup_fields as authoritative.
                - Omit optional null/blank fields; reject a record missing a required field. Do not add null placeholders.
                - Fulfil each download with absolute HTTP(S) URLs under its declared selector. Follow detail_page only for declared resources/content absent from list data. For news, separate cover, body images and document attachments, exclude cover from images, retain useful labels and dedupe normalized URLs. Preserve declared patent PDF/figure/thumbnail and intelligence document/dataset resources; invent no navwarn assets. Resolve internal media IDs only through a verified endpoint.
                - Keep retries bounded and use HttpClient's shared proxy failure/rotation path. Output valid, complete, minimal Python.

                Same-data-type adapter summaries (patterns only):
                {reference_adapters}

                YAML template:
                {template_yaml}

                User refinement request:
                {user_request}

                Existing adapter to preserve unless the request or YAML contract requires a change:
                {existing_adapter_code}

                Return only a fenced ```python block.
            """,
        )

    async def generate_adapter(
        self,
        template_name: str,
        template_yaml: str,
        on_chunk: Callable[[str], None] | None = None,
        user_request: str = "",
        existing_adapter_code: str = "",
    ) -> AdapterResult:
        data_type = self._template_data_type(template_yaml)
        prompt = self.render_prompt(
            "generate_adapter",
            template_yaml=template_yaml,
            reference_adapters=json.dumps(
                await self._reference_adapter_summaries(data_type),
                ensure_ascii=False,
                indent=2,
            ),
            user_request=user_request or "(none)",
            existing_adapter_code=existing_adapter_code or "(none)",
        )
        streamed_response = ""
        streamed_code_length = 0
        chunk_handler = on_chunk

        def forward_code_chunk(chunk: str) -> None:
            nonlocal streamed_response, streamed_code_length
            if chunk_handler is None:
                return
            streamed_response += chunk
            streamable_code = self._extract_streamable_code(streamed_response)
            if len(streamable_code) <= streamed_code_length:
                return
            chunk_handler(streamable_code[streamed_code_length:])
            streamed_code_length = len(streamable_code)

        response = await self.generate(
            prompt,
            max_tokens=8192,
            on_chunk=forward_code_chunk if chunk_handler else None,
        )

        code = self._extract_code(response)
        warnings = self._validate_code(code)

        return AdapterResult(
            adapter_code=code,
            warnings=warnings,
            adapter_name=f"{template_name}_adapter",
        )

    @staticmethod
    def _extract_streamable_code(response: str) -> str:
        stripped = response.lstrip()
        if not stripped or "```".startswith(stripped):
            return ""
        if not stripped.startswith("```"):
            return response

        first_line_end = stripped.find("\n")
        if first_line_end < 0:
            return ""
        code = stripped[first_line_end + 1 :]
        if closing_fence := re.search(r"\s*```\s*$", code):
            return code[: closing_fence.start()]
        return re.sub(r"\s*`{0,2}$", "", code)

    @staticmethod
    def _template_data_type(template_yaml: str) -> str:
        try:
            template = yaml.safe_load(template_yaml)
        except yaml.YAMLError:
            return "other"
        return (
            str(template.get("data_type") or "other")
            if isinstance(template, dict)
            else "other"
        )

    @staticmethod
    async def _reference_adapter_summaries(data_type: str) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        definitions = await ai_collect_store.list_template_definitions(
            include_adapter_code=True
        )
        for definition in definitions:
            try:
                template = yaml.safe_load(str(definition["yaml_content"]))
            except yaml.YAMLError:
                continue
            if (
                not isinstance(template, dict)
                or str(template.get("data_type")) != data_type
            ):
                continue

            adapter_name = str(template.get("adapter") or "")
            source = str(definition.get("adapter_code") or "")
            class_match = re.search(r"class\s+\w+\(([^)]+)\)", source)
            hooks = re.findall(
                r"^\s+(?:async\s+)?def\s+(on_\w+|parse_list_response|build_batch_param_value)\s*\(",
                source,
                re.MULTILINE,
            )
            downloads = template.get("download") or []
            if isinstance(downloads, dict):
                downloads = [downloads]
            summaries.append(
                {
                    "template": template.get("name"),
                    "adapter": adapter_name,
                    "base_class": (
                        class_match.group(1).strip()
                        if class_match
                        else "BaseSiteAdapter"
                    ),
                    "hooks": list(dict.fromkeys(hooks)),
                    "params": [
                        item.get("name")
                        for item in (template.get("params") or [])
                        if isinstance(item, dict) and item.get("name")
                    ],
                    "list_fields": [
                        item.get("name")
                        for item in (template.get("list_fields") or [])
                        if isinstance(item, dict) and item.get("name")
                    ],
                    "detail_fields": [
                        item.get("name")
                        for item in (template.get("detail_fields") or [])
                        if isinstance(item, dict) and item.get("name")
                    ],
                    "dedup_fields": list(template.get("dedup_fields") or []),
                    "resources": [
                        {
                            "selector": item.get("selector"),
                            "asset_type": item.get("asset_type", "asset"),
                        }
                        for item in downloads
                        if isinstance(item, dict)
                    ],
                    "uses_news_media_helpers": "NewsBaseAdapter" in source,
                }
            )
        return summaries[:8]

    def _extract_code(self, response: str) -> str:
        match = re.search(r"```python\s*([\s\S]*?)\s*```", response)
        if match:
            return match.group(1).strip()

        match = re.search(r"```\s*([\s\S]*?)\s*```", response)
        if match:
            return match.group(1).strip()

        return response.strip()

    @staticmethod
    def _validate_code(code: str) -> list[str]:
        warnings = []

        if not code:
            warnings.append("Generated code is empty")
            return warnings

        if "class " not in code:
            warnings.append("Missing adapter class definition")

        if "def " not in code:
            warnings.append("Missing method definition")

        if re.search(r"(?:_first_value|\bfirst_value)\s*\(", code):
            warnings.append("Avoid multi-key field fallback helpers; use YAML selectors for one verified source field")

        if "parse_list_response" in code and (
            "startswith(" in code or ".lower()" in code or "content[:" in code
        ):
            warnings.append("Do not sniff response content in parse_list_response; follow the YAML response_type")

        if re.search(
            r"warning_no\s*=\s*f[\"']|[\"']warning_no[\"']\s*:\s*f[\"']|f[\"'][^\n]*warning_no",
            code,
        ):
            warnings.append("Do not synthesize warning_no in the collection adapter; preserve the source value")

        if (
            not code.strip().endswith(")")
            and not code.strip().endswith(":")
            and not code.strip().endswith('"')
            and not code.strip().endswith("'")
        ):
            warnings.append("Code may be truncated")

        return warnings


adapter_agent = AdapterAgent()
