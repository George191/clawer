from __future__ import annotations

import ast
import asyncio
import ipaddress
import json
import re
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import yaml
from lxml import html as lxml_html

from app.config.settings import settings
from app.downloader.http_client import HttpClient
from app.models.template import SiteTemplate
from app.web.services.browser_renderer import browser_renderer
from app.web.services.site_analyzer import AnalysisResult, SiteAnalyzer

_MODEL_PATH = Path(__file__).resolve().parents[3] / "models" / "Qwen" / "2.5-0.5B-Instruct"
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
_ALLOWED_IMPORTS = {
    "__future__",
    "typing",
    "urllib.parse",
    "lxml",
    "app.adapters",
}
_FORBIDDEN_CALLS = {"eval", "exec", "compile", "open", "input", "__import__"}
_FORBIDDEN_MODULES = {"os", "sys", "subprocess", "socket", "pathlib", "shutil", "ctypes"}


@dataclass
class PreflightResult:
    ok: bool
    normalized_url: str
    host: str = ""
    title: str = ""
    requires_proxy: bool = False
    proxy_mode: str = "direct"
    preview_html: str = ""
    preview_image: str = ""
    rendered_by: str = "http"
    network_endpoints: list[str] | None = None
    error_code: str = ""
    error_message: str = ""
    html: str = ""
    checked_at: float = 0.0

    def public_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "normalizedUrl": self.normalized_url,
            "host": self.host,
            "title": self.title,
            "requiresProxy": self.requires_proxy,
            "proxyMode": self.proxy_mode,
            "previewHtml": self.preview_html,
            "previewImage": self.preview_image,
            "renderedBy": self.rendered_by,
            "networkEndpoints": self.network_endpoints or [],
            "errorCode": self.error_code,
            "errorMessage": self.error_message,
        }


class LocalQwenPolicy:
    """Qwen scores one closed decision; it never writes files, prose, commands, or code."""

    def __init__(self) -> None:
        self._model: Any = None
        self._tokenizer: Any = None
        self._load_lock = threading.Lock()

    def _load(self) -> None:
        if self._model is not None:
            return
        with self._load_lock:
            if self._model is not None:
                return
            if not (_MODEL_PATH / "model.safetensors").is_file():
                raise RuntimeError(f"Local Qwen model is incomplete: {_MODEL_PATH}")
            from transformers import AutoModelForCausalLM, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(_MODEL_PATH, local_files_only=True)
            self._model = AutoModelForCausalLM.from_pretrained(
                _MODEL_PATH,
                local_files_only=True,
                dtype="auto",
                low_cpu_mem_usage=True,
            )
            self._model.eval()

    def _decide_sync(self, result: AnalysisResult, prompt: str = "") -> dict[str, Any]:
        self._load()
        import torch

        decision = {
            "template_name": result.template_name,
            "response_type": result.template_dict.get("response_type", "html"),
            "requires_adapter": bool(result.adapter_code),
        }
        model_prompt = (
            "Crawler artifact boundary review. Only template YAML and one matching Python adapter are allowed.\n"
            f"User collection intent: {prompt.strip()[:1000] or 'not specified'}\n"
            f"Observed domain: {result.domain}\n"
            f"Observed acquisition: {json.dumps(result.acquisition.response_dict(), ensure_ascii=False, sort_keys=True)}\n"
            f"Observed fields: {', '.join(field.name for field in result.fields)}\n"
            f"Closed decision: {json.dumps(decision, ensure_ascii=False, sort_keys=True)}"
        )
        encoded = self._tokenizer(model_prompt, return_tensors="pt")
        with torch.inference_mode():
            scored = self._model(**encoded, labels=encoded["input_ids"])
        if not torch.isfinite(scored.loss):
            raise RuntimeError("Qwen produced a non-finite artifact boundary score")
        return decision

    async def decide(self, result: AnalysisResult, prompt: str = "") -> dict[str, Any]:
        return await asyncio.to_thread(self._decide_sync, result, prompt)


class TemplateAdapterAgent:
    def __init__(self) -> None:
        self._policy = LocalQwenPolicy()
        self._preflight_cache: dict[str, PreflightResult] = {}

    async def preflight(self, url: str) -> PreflightResult:
        try:
            normalized, host, dns_resolved = await self._validate_url(url)
        except ValueError as exc:
            return PreflightResult(
                ok=False,
                normalized_url=url.strip(),
                error_code="INVALID_URL",
                error_message=str(exc),
                checked_at=time.time(),
            )

        client = HttpClient()
        requires_proxy = False
        proxy_mode = "direct"
        browser_title = ""
        preview_image = ""
        rendered_by = "http"
        network_endpoints: list[str] = []
        try:
            try:
                if not dns_resolved:
                    raise RuntimeError("The URL host requires proxy-side DNS resolution")
                page_html = await client.request_page(normalized, force_direct=True, no_timeout=True)
            except Exception as direct_error:
                if not self._proxy_configured():
                    raise RuntimeError(f"Website cannot be opened directly: {direct_error}") from direct_error
                requires_proxy = True
                proxy_mode = "configured_proxy"
                try:
                    page_html = await client.request_page(normalized, anti_crawl_enabled=True, no_timeout=True)
                except Exception as proxy_error:
                    raise RuntimeError(
                        f"Website cannot be opened directly or through the configured proxy: {proxy_error}"
                    ) from proxy_error
        except Exception as exc:
            return PreflightResult(
                ok=False,
                normalized_url=normalized,
                host=host,
                requires_proxy=requires_proxy,
                proxy_mode=proxy_mode,
                error_code="PAGE_UNREACHABLE",
                error_message=str(exc),
                checked_at=time.time(),
            )
        finally:
            await client.close()

        if browser_renderer.available():
            browser_result = None
            try:
                browser_result = await browser_renderer.render(normalized)
            except Exception:
                if self._proxy_configured():
                    try:
                        browser_result = await browser_renderer.render(normalized, use_proxy=True)
                        requires_proxy = True
                        proxy_mode = "configured_proxy"
                    except Exception:
                        browser_result = None
            if browser_result is not None and urlparse(browser_result.url).hostname == host:
                page_html = browser_result.html
                browser_title = browser_result.title
                preview_image = browser_result.screenshot_data_url
                network_endpoints = browser_result.json_endpoints
                rendered_by = "chrome"

        if not page_html or len(page_html.strip()) < 80:
            return PreflightResult(
                ok=False,
                normalized_url=normalized,
                host=host,
                requires_proxy=requires_proxy,
                proxy_mode=proxy_mode,
                error_code="EMPTY_PAGE",
                error_message="The website returned no usable page content",
                checked_at=time.time(),
            )

        barrier = SiteAnalyzer.detect_page_barrier(page_html)
        if barrier:
            return PreflightResult(
                ok=False,
                normalized_url=normalized,
                host=host,
                requires_proxy=requires_proxy,
                proxy_mode=proxy_mode,
                error_code="BROWSER_RENDER_REQUIRED",
                error_message=barrier,
                checked_at=time.time(),
            )

        title, preview_html = self._build_preview(page_html, normalized)
        result = PreflightResult(
            ok=True,
            normalized_url=normalized,
            host=host,
            title=browser_title or title or host,
            requires_proxy=requires_proxy,
            proxy_mode=proxy_mode,
            preview_html=preview_html,
            preview_image=preview_image,
            rendered_by=rendered_by,
            network_endpoints=network_endpoints,
            html=page_html,
            checked_at=time.time(),
        )
        self._preflight_cache[normalized] = result
        return result

    async def generate(self, url: str, prompt: str = "") -> tuple[AnalysisResult, dict[str, Any]]:
        preflight = self._preflight_cache.get(url)
        if preflight is None or not preflight.ok or time.time() - preflight.checked_at > 300:
            preflight = await self.preflight(url)
        if not preflight.ok:
            raise ValueError(preflight.error_message or "URL preflight failed")

        analyzer = SiteAnalyzer()
        try:
            result = await analyzer.analyze_html(
                preflight.normalized_url,
                preflight.html,
                prompt,
                preflight.network_endpoints,
            )
        finally:
            await analyzer.close()

        decision = await self._policy.decide(result, prompt)
        self._validate_model_decision(result, decision)
        self._validate_template(result.template_yaml, preflight.host)
        self._validate_adapter(result.adapter_code, result.template_name)
        return result, {
            "model": "Qwen2.5-0.5B-Instruct",
            "decision": decision,
            "requiresProxy": preflight.requires_proxy,
            "proxyMode": preflight.proxy_mode,
            "pageTitle": preflight.title,
            "prompt": prompt.strip()[:2000],
            "acquisition": result.acquisition.response_dict(),
        }

    def validate_template_document(self, template_yaml: str) -> dict[str, Any]:
        raw = yaml.safe_load(template_yaml)
        if not isinstance(raw, dict):
            raise ValueError("Template must be a YAML mapping")
        host = urlparse(str(raw.get("base_url", ""))).hostname or ""
        self._validate_template(template_yaml, host)
        return raw

    def validate_release_artifacts(
        self,
        template_yaml: str,
        template_name: str,
        domain: str,
        adapter_path: str,
    ) -> None:
        raw = self.validate_template_document(template_yaml)
        if raw.get("name") != template_name:
            raise ValueError("Release name must match template YAML name")
        if urlparse(str(raw.get("base_url", ""))).hostname != domain:
            raise ValueError("Release domain must match template YAML base_url")
        allowed_adapter_path = f"app/adapters/{template_name}.py"
        if adapter_path and adapter_path != allowed_adapter_path:
            raise ValueError(f"Adapter path must be {allowed_adapter_path}")

    def publish_artifacts(self, template_yaml: str, adapter_code: str) -> dict[str, str]:
        raw = self.validate_template_document(template_yaml)
        template_name = str(raw["name"])
        self._validate_adapter(adapter_code, template_name)
        template_path = (Path(settings.template_dir) / f"{template_name}.yaml").resolve()
        adapter_path = (Path(__file__).resolve().parents[2] / "adapters" / f"{template_name}.py").resolve()
        template_root = Path(settings.template_dir).resolve()
        adapter_root = (Path(__file__).resolve().parents[2] / "adapters").resolve()
        if template_path.parent != template_root or adapter_path.parent != adapter_root:
            raise ValueError("Generated artifact path escaped its allowed directory")
        if template_path.exists() or (adapter_code and adapter_path.exists()):
            raise ValueError("The agent cannot overwrite an existing template or adapter")

        template_path.write_text(template_yaml, encoding="utf-8")
        try:
            if adapter_code:
                adapter_path.write_text(adapter_code, encoding="utf-8")
        except Exception:
            template_path.unlink(missing_ok=True)
            raise
        return {
            "template": template_path.as_posix(),
            "adapter": adapter_path.as_posix() if adapter_code else "",
        }

    async def _validate_url(self, url: str) -> tuple[str, str, bool]:
        value = url.strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Only complete HTTP/HTTPS URLs are supported")
        if parsed.username or parsed.password:
            raise ValueError("Credentials are not allowed in target URLs")

        host = parsed.hostname.rstrip(".").lower()
        dns_resolved = True
        try:
            address = ipaddress.ip_address(host)
            self._assert_public_ip(address)
        except ValueError:
            if "." not in host or not all(part and len(part) <= 63 for part in host.split(".")):
                raise ValueError("The URL host is not a valid public domain") from None
            if host.endswith((".local", ".internal", ".localhost")):
                raise ValueError("Local and internal domains are not allowed") from None
            loop = asyncio.get_running_loop()
            try:
                records = await loop.run_in_executor(
                    None,
                    lambda: socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM),
                )
            except socket.gaierror as exc:
                if not self._proxy_configured():
                    raise ValueError("The URL host cannot be resolved") from exc
                dns_resolved = False
                records = []
            addresses = {record[4][0] for record in records}
            if dns_resolved and not addresses:
                raise ValueError("The URL host cannot be resolved") from None
            for resolved in addresses:
                self._assert_public_ip(ipaddress.ip_address(resolved))

        normalized = urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", parsed.params, parsed.query, ""))
        return normalized, host, dns_resolved

    @staticmethod
    def _assert_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
        if not address.is_global:
            raise ValueError("Private, loopback, link-local and reserved addresses are not allowed")

    @staticmethod
    def _proxy_configured() -> bool:
        return bool(settings.tunnel_proxy_url or settings.proxy_pool_file or settings.proxy_pool_api_url)

    @staticmethod
    def _build_preview(page_html: str, url: str) -> tuple[str, str]:
        try:
            tree = lxml_html.fromstring(page_html, base_url=url)
        except Exception as exc:
            raise ValueError("The response is not valid HTML") from exc
        title = " ".join(tree.xpath("//title[1]//text()") or []).strip()
        for node in tree.xpath("//script|//meta|//base|//form|//iframe|//object|//embed|//link[not(contains(translate(@rel, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'stylesheet'))]"):
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
        head = next(iter(tree.xpath("//head")), None)
        if head is not None:
            base = lxml_html.Element("base", href=url)
            head.insert(0, base)
        for node in tree.iter():
            for attr in list(node.attrib):
                if attr.lower().startswith("on") or attr.lower() in {"srcdoc", "action", "formaction"}:
                    del node.attrib[attr]
            if node.get("href"):
                node.set("href", urljoin(url, node.get("href")))
            if node.get("src"):
                node.set("src", urljoin(url, node.get("src")))
        rendered = lxml_html.tostring(tree, encoding="unicode", method="html")
        return title[:200], rendered[:120_000]

    @staticmethod
    def _validate_model_decision(result: AnalysisResult, decision: dict[str, Any]) -> None:
        expected = {
            "template_name": result.template_name,
            "response_type": result.template_dict.get("response_type", "html"),
            "requires_adapter": bool(result.adapter_code),
        }
        if decision != expected:
            raise ValueError("Qwen output violated the constrained artifact decision schema")

    @staticmethod
    def _validate_template(template_yaml: str, expected_host: str) -> None:
        if len(template_yaml.encode("utf-8")) > 64 * 1024:
            raise ValueError("Generated template exceeds 64 KiB")
        raw = yaml.safe_load(template_yaml)
        if not isinstance(raw, dict):
            raise ValueError("Generated template must be a YAML mapping")
        if not _NAME_RE.fullmatch(str(raw.get("name", ""))):
            raise ValueError("Generated template name is invalid")
        if urlparse(str(raw.get("base_url", ""))).hostname != expected_host:
            raise ValueError("Generated template base_url escaped the preflight host")
        if len(raw.get("list_fields") or []) > 100 or len(raw.get("detail_fields") or []) > 100:
            raise ValueError("Generated template contains too many fields")
        SiteTemplate(**raw)

    @staticmethod
    def _validate_adapter(code: str, template_name: str) -> None:
        if not code:
            return
        if len(code.encode("utf-8")) > 64 * 1024 or len(code.splitlines()) > 500:
            raise ValueError("Generated adapter exceeds the code limit")
        tree = ast.parse(code)
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        if len(classes) != 1 or not any(
            isinstance(base, ast.Name) and base.id == "BaseSiteAdapter" for base in classes[0].bases
        ):
            raise ValueError("Generated adapter must define exactly one BaseSiteAdapter subclass")
        for node in ast.walk(tree):
            if isinstance(node, ast.Import | ast.ImportFrom):
                modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for module in modules:
                    root = module.split(".")[0]
                    if root in _FORBIDDEN_MODULES or not any(
                        module == allowed or module.startswith(f"{allowed}.") for allowed in _ALLOWED_IMPORTS
                    ):
                        raise ValueError(f"Generated adapter import is not allowed: {module}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                raise ValueError(f"Generated adapter call is forbidden: {node.func.id}")
        decorator_names = [
            decorator.args[0].value
            for decorator in classes[0].decorator_list
            if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "register_adapter"
            and len(decorator.args) == 1
            and isinstance(decorator.args[0], ast.Constant)
        ]
        if decorator_names != [template_name]:
            raise ValueError("Generated adapter registration does not match the template name")


template_adapter_agent = TemplateAdapterAgent()
