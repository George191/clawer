"""Validation utilities for web API."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException

from app.config.settings import settings
from app.logger import get_logger

logger = get_logger(__name__)

AI_COLLECT_SCOPE_PATH = Path(__file__).resolve().parent.parent / "policies" / "ai_collect_scope.json"
DEFAULT_AI_COLLECT_SCOPE: dict[str, Any] = {
    "url_rules": {
        "allowed_schemes": ["http", "https"],
        "blocked_exact_hosts": ["localhost", "127.0.0.1", "0.0.0.0", "::1"],
        "blocked_prefix_hosts": ["10.", "192.168."],
        "blocked_172_range": [16, 31],
    },
    "limits": {
        "max_template_pages": 100,
        "max_dry_run_limit": 100,
        "max_generated_adapter_lines": 500,
    },
    "adapter_rules": {
        "forbidden_patterns": [
            "eval(",
            "child_process",
            "process.env",
            "/etc/",
            "/proc/",
            "/.ssh/",
        ],
    },
}


def _load_ai_collect_scope() -> dict[str, Any]:
    scope = json.loads(json.dumps(DEFAULT_AI_COLLECT_SCOPE))
    if not AI_COLLECT_SCOPE_PATH.exists():
        return scope

    try:
        loaded = json.loads(AI_COLLECT_SCOPE_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to load AI collect scope file: %s", AI_COLLECT_SCOPE_PATH)
        return scope

    for key, default_value in scope.items():
        loaded_value = loaded.get(key)
        if isinstance(default_value, dict) and isinstance(loaded_value, dict):
            scope[key] = {**default_value, **loaded_value}
        elif loaded_value is not None:
            scope[key] = loaded_value
    return scope


AI_COLLECT_SCOPE = _load_ai_collect_scope()


def scope_limit(name: str, default: int) -> int:
    """Get a numeric limit from AI collect scope config."""
    raw = AI_COLLECT_SCOPE.get("limits", {}).get(name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, value)


def clamp_positive(value: int, limit_name: str, default: int) -> int:
    """Clamp a positive integer value within configured limits."""
    return max(1, min(value, scope_limit(limit_name, default)))


def adapter_forbidden_patterns() -> list[str]:
    """Get list of forbidden patterns for generated adapter code."""
    patterns = AI_COLLECT_SCOPE.get("adapter_rules", {}).get("forbidden_patterns", [])
    return [str(pattern) for pattern in patterns if str(pattern).strip()]


def validate_generated_adapter(code: str) -> None:
    """Validate generated adapter code against security rules."""
    max_lines = scope_limit("max_generated_adapter_lines", 500)
    line_count = len(code.splitlines())
    errors: list[str] = []

    if line_count > max_lines:
        errors.append(f"代码行数超限 ({line_count} > {max_lines})")

    for forbidden in adapter_forbidden_patterns():
        if forbidden in code:
            errors.append(f"检测到禁止模式: {forbidden}")

    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        errors.append(f"Python syntax error: {exc.msg}")
    else:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            imports_http_client = any(alias.name == "HttpClient" for alias in node.names)
            if imports_http_client and node.module != "app.downloader.http_client":
                errors.append(
                    "HttpClient must be imported from app.downloader.http_client"
                )

    if errors:
        raise HTTPException(status_code=400, detail=f"安全校验失败: {'; '.join(errors)}")


def validate_target_url(url: str) -> None:
    """Validate target URL against security rules."""
    parsed = urlparse(url)
    url_rules = AI_COLLECT_SCOPE.get("url_rules", {})
    allowed_schemes = set(url_rules.get("allowed_schemes", ["http", "https"]))

    if parsed.scheme not in allowed_schemes:
        raise HTTPException(status_code=400, detail=f"不支持的协议: {parsed.scheme}")

    hostname = parsed.hostname or ""

    if hostname in set(url_rules.get("blocked_exact_hosts", [])):
        raise HTTPException(status_code=400, detail="禁止访问本地地址")

    if any(hostname.startswith(prefix) for prefix in url_rules.get("blocked_prefix_hosts", [])):
        raise HTTPException(status_code=400, detail="禁止访问内网地址")

    if hostname.startswith("172."):
        try:
            second = int(hostname.split(".")[1])
            blocked_172_range = url_rules.get("blocked_172_range", [16, 31])
            range_start = int(blocked_172_range[0])
            range_end = int(blocked_172_range[1])
            if range_start <= second <= range_end:
                raise HTTPException(status_code=400, detail="禁止访问内网地址")
        except (IndexError, TypeError, ValueError):
            pass
