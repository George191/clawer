from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.web.agents.base import BaseAgent

logger = logging.getLogger(__name__)


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
        model_path = Path(__file__).parents[3] / "models" / "Qwen" / "2.5-Coder-1.5B-Instruct"
        super().__init__(str(model_path))
        self._register_default_prompts()

    def _register_default_prompts(self) -> None:
        self.register_prompt(
            "generate_adapter",
            """Generate a Python site adapter for the supplied YAML template.

Project constraints:
1. Treat the YAML as the complete contract. Emit exactly its business fields and adapter-produced resource fields; do not restore source IDs, duplicate aliases or metadata that the template deliberately omitted.
2. Import BaseSiteAdapter/register_adapter from app.adapters and use the injected HttpClient. For data_type=news, prefer the existing NewsBaseAdapter and its URL/media dedupe helpers when applicable. Do not introduce requests, aiohttp, clients, dependencies or standalone crawler code.
3. Implement only site-specific hooks the YAML requires: parse_list_response, on_before_crawl, on_after_page, on_error, on_request_headers, on_page_advance or build_batch_param_value. Leave selectors and ordinary parsing to the template engine.
4. For a dynamic page, parse the configured verified API rather than the rendered shell. Validate response shape, item container and required fields. Maintenance, CAPTCHA, login, WAF and unexpected error HTML must raise or activate only a YAML-documented official fallback; zero parsed records from a non-empty unexpected body is not success.
5. Preserve required params without silent defaults. Normalize only fields needed by list_fields, dedup_fields and download selectors. The YAML dedup_fields are authoritative: ensure each is non-empty and stable, and do not add backup fields when one key is unique.
6. Fulfil every download item. Output actual absolute HTTP(S) URLs under the declared selector. For news, resolve cover/thumbnail metadata, extract body images separately, exclude the cover from images, extract document attachments (PDF/DOC/XLS/PPT/ZIP and declared types), retain useful labels/alt text, and deduplicate by normalized URL. Follow detail_page only when resources or reconstructive content are absent from the list/API.
7. For patents preserve declared PDF/figure/thumbnail resources; for intelligence preserve declared document/dataset resources; for navwarn do not invent assets. Never treat an internal numeric media ID as a downloadable URL—resolve it through a verified endpoint when the YAML requires adapter enrichment.
8. Keep retries bounded and use the shared proxy failure/rotation path through HttpClient. Generate valid complete Python, use project logging, and add no unrelated framework code.

Same data-type project conventions:
{reference_adapters}

References describe reusable project patterns only. The supplied YAML wins; never copy another site's URLs, selectors, constants or fields.

YAML template:
{template_yaml}

Return only a fenced ```python block.
""",
        )

    async def generate_adapter(
        self, template_name: str, template_yaml: str
    ) -> AdapterResult:
        data_type = self._template_data_type(template_yaml)
        prompt = self.render_prompt(
            "generate_adapter",
            template_yaml=template_yaml,
            reference_adapters=json.dumps(
                self._reference_adapter_summaries(data_type),
                ensure_ascii=False,
                indent=2,
            ),
        )
        response = await self.generate(prompt, max_tokens=8192)

        code = self._extract_code(response)
        warnings = self._validate_code(code)

        return AdapterResult(
            adapter_code=code,
            warnings=warnings,
            adapter_name=f"{template_name}_adapter",
        )

    @staticmethod
    def _template_data_type(template_yaml: str) -> str:
        try:
            template = yaml.safe_load(template_yaml)
        except yaml.YAMLError:
            return "other"
        return str(template.get("data_type") or "other") if isinstance(template, dict) else "other"

    @staticmethod
    def _reference_adapter_summaries(data_type: str) -> list[dict[str, Any]]:
        project_root = Path(__file__).parents[3]
        summaries: list[dict[str, Any]] = []
        for path in sorted((project_root / "templates").glob("*.y*ml")):
            try:
                template = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, yaml.YAMLError):
                continue
            if not isinstance(template, dict) or str(template.get("data_type")) != data_type:
                continue

            adapter_name = str(template.get("adapter") or "")
            adapter_path = project_root / "app" / "adapters" / f"{adapter_name}.py"
            source = ""
            if adapter_name and adapter_path.is_file():
                try:
                    source = adapter_path.read_text(encoding="utf-8")
                except OSError:
                    source = ""
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
                    "base_class": class_match.group(1).strip() if class_match else "BaseSiteAdapter",
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

    def _validate_code(self, code: str) -> list[str]:
        warnings = []

        if not code:
            warnings.append("Generated code is empty")
            return warnings

        if "class " not in code:
            warnings.append("Missing adapter class definition")

        if "def " not in code:
            warnings.append("Missing method definition")

        if (
            not code.strip().endswith(")")
            and not code.strip().endswith(":")
            and not code.strip().endswith('"')
            and not code.strip().endswith("'")
        ):
            warnings.append("Code may be truncated")

        return warnings
adapter_agent = AdapterAgent()
