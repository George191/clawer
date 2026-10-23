"""Shared Chromium launch configuration for rendering and collection."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from app.config.ai_settings import ai_settings

_CHROME_PATHS = (
    Path("/usr/bin/chromium"),
    Path("/usr/bin/chromium-browser"),
    Path("/usr/bin/google-chrome"),
)


class BrowserRuntimeError(RuntimeError):
    """Browser configuration prevents starting the runtime."""


def browser_executable_path(playwright: object) -> str | None:
    configured_path = str(ai_settings.browser_executable_path or "").strip()
    if configured_path:
        path = Path(configured_path)
        if not path.is_file():
            raise BrowserRuntimeError(
                f"Configured browser executable does not exist: {configured_path}"
            )
        return str(path)

    # An explicit Playwright channel must take precedence over system paths.
    if str(ai_settings.browser_channel or "").strip():
        return None

    system_path = next((path for path in _CHROME_PATHS if path.is_file()), None)
    if system_path is not None:
        return str(system_path)

    bundled_path = getattr(getattr(playwright, "chromium", None), "executable_path", "")
    if bundled_path and Path(str(bundled_path)).is_file():
        return str(bundled_path)
    return None


def browser_proxy_config(proxy_url: str) -> dict[str, str] | None:
    if not proxy_url:
        return None
    try:
        parsed = urlparse(proxy_url)
        if not parsed.scheme or not parsed.hostname or not parsed.port:
            raise ValueError("Missing proxy scheme, host or port")
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        config = {"server": f"{parsed.scheme}://{host}:{parsed.port}"}
        if parsed.username:
            config["username"] = unquote(parsed.username)
        if parsed.password:
            config["password"] = unquote(parsed.password)
        return config
    except ValueError as exc:
        raise BrowserRuntimeError("Configured browser proxy URL is invalid") from exc


def browser_launch_options(
    playwright: object, *, timeout_ms: int, proxy_url: str = ""
) -> dict[str, object]:
    if not ai_settings.browser_enabled:
        raise BrowserRuntimeError("Browser rendering is disabled by SPIDER_AI_BROWSER_ENABLED")

    executable_path = browser_executable_path(playwright)
    options: dict[str, object] = {
        "headless": ai_settings.browser_headless,
        "proxy": browser_proxy_config(proxy_url),
        "timeout": max(1, int(timeout_ms)),
    }
    if executable_path:
        options["executable_path"] = executable_path
    elif ai_settings.browser_channel:
        options["channel"] = ai_settings.browser_channel.strip()

    args = ["--disable-dev-shm-usage"]
    if ai_settings.browser_no_sandbox or (
        os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0
    ):
        args.extend(["--no-sandbox", "--disable-setuid-sandbox"])
    options["args"] = args
    return options
