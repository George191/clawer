"""The complete tool surface: public-page evidence and validated collection artifacts."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

import yaml
from yaml.tokens import AliasToken, AnchorToken

from agent.contracts import CollectionRequest, public_url
from agent.model import CollectionModel
from app.config.ai_settings import ai_settings
from app.models.template import SiteTemplate
from app.web.agents.adapter import AdapterAgent
from app.web.agents.template import TemplateAgent
from app.web.services.browser_renderer import browser_renderer
from app.web.utils.validation import scope_limit, validate_generated_adapter


def reject_yaml_aliases(content: str) -> None:
    # YAML aliases can expand traversal exponentially even within a small model response.
    try:
        if any(isinstance(token, AliasToken | AnchorToken) for token in yaml.scan(content)):
            raise ValueError("Collection artifacts must not contain YAML anchors or aliases")
    except yaml.YAMLError as exc:
        raise ValueError("Invalid collection YAML") from exc


class ScopedTemplateAgent(TemplateAgent):
    def __init__(self, model: CollectionModel):
        super().__init__()
        self.model = model

    async def generate(self, prompt: str, max_tokens: int = 8192, on_chunk=None) -> str:
        response = await self.model.generate(prompt, max_tokens=max_tokens, on_chunk=on_chunk)
        reject_yaml_aliases(self._extract_yaml(response))
        return response

    @staticmethod
    async def _reference_template_summaries(*_args) -> list:
        # Do not read another user's workspace artifacts as model context.
        return []


class ScopedAdapterAgent(AdapterAgent):
    def __init__(self, model: CollectionModel):
        super().__init__()
        self.generate = model.generate

    @staticmethod
    async def _reference_adapter_summaries(*_args) -> list:
        return []


class CollectionTools:
    def __init__(self, model: CollectionModel):
        self.model = model
        self.template_agent = ScopedTemplateAgent(model)
        self.adapter_agent = ScopedAdapterAgent(model)

    async def capture(self, request: CollectionRequest, checkpoints: dict) -> dict:
        public_url(request.url)
        rendered = await browser_renderer.render(request.url)
        public_url(rendered.url)
        evidence = self.template_agent.build_page_evidence(
            rendered.url, rendered.html, rendered.json_endpoints, rendered.network_responses, [],
        )
        if len(rendered.html.encode()) > 262144:
            raise ValueError("Page evidence exceeds the harness limit")
        return {"url": rendered.url, "title": rendered.title[:500], "evidence": evidence,
                "html_sha256": hashlib.sha256(rendered.html.encode()).hexdigest()}

    async def template(self, request: CollectionRequest, checkpoints: dict) -> dict:
        capture = checkpoints["capture"]
        goal = f"{request.goal}\nHard collection limits: at most {request.max_pages} pages and {request.max_fields} fields."
        content = await self.template_agent.generate_template(
            capture["url"], capture["evidence"], capture["title"], user_request=goal,
        )
        self.validate_template(content, request, capture)
        return {"yaml": content, "usage": dict(self.model.usage)}

    async def adapter(self, request: CollectionRequest, checkpoints: dict) -> dict:
        content = checkpoints["template"]["yaml"]
        template = SiteTemplate.model_validate(yaml.safe_load(content))
        if not template.adapter:
            return {"code": "", "required": False}
        result = await self.adapter_agent.generate_adapter(template.name, content, user_request=request.goal)
        if result.warnings:
            raise ValueError("Generated adapter failed static validation")
        if len(result.adapter_code.encode()) > scope_limit("max_generated_adapter_bytes", 65536):
            raise ValueError("Generated adapter exceeds the byte limit")
        validate_generated_adapter(result.adapter_code)
        return {"code": result.adapter_code, "required": True, "usage": dict(self.model.usage)}

    async def validate(self, request: CollectionRequest, checkpoints: dict) -> dict:
        content = checkpoints["template"]["yaml"]
        template = self.validate_template(content, request, checkpoints["capture"])
        adapter = checkpoints["adapter"]
        if template.adapter and not adapter["code"]:
            raise ValueError("Required adapter is missing")
        if adapter["code"]:
            validate_generated_adapter(adapter["code"])
        return {
            "template_yaml": content, "adapter_python": adapter["code"],
            "source_url": checkpoints["capture"]["url"],
            "evidence_sha256": checkpoints["capture"]["html_sha256"],
            "template_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "adapter_sha256": hashlib.sha256(adapter["code"].encode()).hexdigest(),
            "validation": {"schema": True, "static_adapter": "passed" if adapter["code"] else "not_required", "adapter_executed": False},
            "requires_review": True, "published": False,
        }

    @staticmethod
    def validate_template(content: str, request: CollectionRequest, capture: dict) -> SiteTemplate:
        if len(content.encode()) > scope_limit("max_generated_template_bytes", 65536):
            raise ValueError("Generated template exceeds the byte limit")
        reject_yaml_aliases(content)
        raw = yaml.safe_load(content)
        if not isinstance(raw, dict) or set(raw) - set(SiteTemplate.model_fields):
            raise ValueError("Generated template contains unsupported keys")
        template = SiteTemplate.model_validate(raw)
        public_url(template.base_url)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", template.name) or (template.adapter and template.adapter != template.name):
            raise ValueError("Artifact names must identify this collection template only")
        fields = template.list_fields + template.detail_fields
        names = [field.name for field in fields]
        if not names or any(len(group) != len({field.name for field in group}) for group in (template.list_fields, template.detail_fields)) or len(set(names)) > min(request.max_fields, scope_limit("max_generated_fields", 100)):
            raise ValueError("Generated fields are empty, duplicated or over budget")
        if template.list_pagination and template.list_pagination.max_pages > request.max_pages:
            raise ValueError("Generated pagination exceeds the requested page budget")
        if not set(template.dedup_fields).issubset(names):
            raise ValueError("Dedup fields must be present in the collection contract")
        origins = {urlsplit(capture["url"]).hostname}
        origins.update(urlsplit(value).hostname for value in capture["evidence"].get("api_endpoints", []))

        def check(value):
            if isinstance(value, str) and value.lower().startswith(("http://", "https://")):
                public_url(value)
                if urlsplit(value).hostname not in origins:
                    raise ValueError("Generated URL has no observed collection origin")
            elif isinstance(value, dict):
                for item in value.values():
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)

        check(raw)
        return template

    def timeout(self, stage: str) -> float:
        if stage == "capture":
            return max(10, ai_settings.page_fetch_timeout * 4)
        return self.model.config.timeout_seconds * 4 + 10
