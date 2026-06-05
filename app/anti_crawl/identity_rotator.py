"""身份随机化 - Identity Rotation.

User-Agent 池管理、Referer 伪造、Cookie 轮换。
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from app.config.settings import settings

logger = logging.getLogger(__name__)

# 内置 UA 池（最新主流浏览器 UA，2024-2025）
_FALLBACK_UA_POOL: list[str] = [
    # Chrome 125+ on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Chrome 125+ on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Chrome 125+ on Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    # Firefox 126+ on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    # Firefox 126+ on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0",
    # Safari 17+ on macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    # Edge 125+ on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    # Chrome on Android
    "Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.6422.53 Mobile Safari/537.36",
    # Safari on iPhone
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
]


class IdentityRotator:
    """身份随机化管理器。

    特性：
    - UA 池管理（内置默认池 + 从文件加载）
    - Referer 自动生成（基于目标 URL 的 base_url）
    - Cookie 轮换（从文件加载 cookie 列表）
    - 请求次数计数，达到阈值自动切换身份
    """

    def __init__(self) -> None:
        self._ua_pool: list[str] = []
        self._cookie_pool: list[dict[str, str]] = []
        self._ua_index: int = 0
        self._cookie_index: int = 0
        self._request_count: int = 0
        self._loaded: bool = False

    @property
    def enabled(self) -> bool:
        return settings.anti_crawl_enabled

    def _ensure_loaded(self) -> None:
        """懒加载 UA 池和 Cookie 池。"""
        if self._loaded:
            return

        # 加载 UA 池
        if settings.user_agent_pool_file:
            path = Path(settings.user_agent_pool_file)
            if path.exists():
                lines = [
                    line.strip()
                    for line in path.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.strip().startswith("#")
                ]
                if lines:
                    self._ua_pool = lines
                    logger.info("Loaded %d user agents from file: %s", len(lines), path)

        if not self._ua_pool:
            self._ua_pool = list(_FALLBACK_UA_POOL)

        # 加载 Cookie 池
        if settings.cookie_pool_file:
            path = Path(settings.cookie_pool_file)
            if path.exists():
                try:
                    self._load_cookies(path)
                except Exception as e:
                    logger.error("Failed to load cookie pool: %s", e)

        self._loaded = True
        logger.info(
            "Identity rotator initialized: %d UAs, %d cookie sets",
            len(self._ua_pool),
            len(self._cookie_pool),
        )

    def _load_cookies(self, path: Path) -> None:
        """从文件加载 Cookie 池。

        支持两种格式：
        1. JSON 数组： [{"name": "session", "value": "abc"}, ...]
        2. Netscape cookie 格式（简版）：每行 name=value
        """
        text = path.read_text(encoding="utf-8").strip()

        import json
        try:
            data = json.loads(text)
            if isinstance(data, list):
                # 如果是 cookie 对数组
                if data and isinstance(data[0], dict) and "name" in data[0]:
                    cookies = {}
                    for item in data:
                        cookies[item["name"]] = item.get("value", "")
                    self._cookie_pool.append(cookies)
                # 如果是 cookie set 的数组
                elif data and isinstance(data[0], dict):
                    for cookie_set in data:
                        if isinstance(cookie_set, dict):
                            self._cookie_pool.append(dict(cookie_set))
                else:
                    self._cookie_pool.append({})
            elif isinstance(data, dict):
                self._cookie_pool.append(dict(data))
        except (json.JSONDecodeError, ValueError):
            # Netscape 格式或 key=value 格式
            cookies: dict[str, str] = {}
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    cookies[key.strip()] = value.strip()
            if cookies:
                self._cookie_pool.append(cookies)

    def get_user_agent(self) -> str:
        """获取一个随机 User-Agent。"""
        self._ensure_loaded()
        if not self._ua_pool:
            return settings.http_user_agent

        if settings.identity_rotation_interval > 0:
            self._request_count += 1
            if self._request_count >= settings.identity_rotation_interval:
                self._ua_index = random.randint(0, len(self._ua_pool) - 1)
                self._request_count = 0
        else:
            # 每次请求都切换
            self._ua_index = (self._ua_index + 1) % len(self._ua_pool)

        return self._ua_pool[self._ua_index]

    def get_referer(self, target_url: Optional[str] = None) -> Optional[str]:
        """根据目标 URL 生成 Referer。

        Args:
            target_url: 目标 URL，用于生成对应的 referer

        Returns:
            Referer URL 字符串，如果 referer 功能关闭则返回 None
        """
        if not settings.referer_enabled:
            return None

        if target_url:
            parsed = urlparse(target_url)
            return f"{parsed.scheme}://{parsed.netloc}/"
        return "https://www.google.com/"

    def get_cookies(self) -> Optional[dict[str, str]]:
        """获取一组 Cookie。"""
        self._ensure_loaded()
        if not self._cookie_pool:
            return None

        if settings.identity_rotation_interval > 0:
            if self._request_count >= settings.identity_rotation_interval:
                self._cookie_index = random.randint(0, len(self._cookie_pool) - 1)
        else:
            self._cookie_index = (self._cookie_index + 1) % len(self._cookie_pool)

        return dict(self._cookie_pool[self._cookie_index])

    def get_headers(self, target_url: Optional[str] = None) -> dict[str, str]:
        """获取完整的随机化请求头。

        Args:
            target_url: 目标 URL，用于生成 Referer

        Returns:
            包含 User-Agent、Referer（可选）等请求头的字典
        """
        self._ensure_loaded()
        headers = {"User-Agent": self.get_user_agent()}

        referer = self.get_referer(target_url)
        if referer:
            headers["Referer"] = referer

        # 常见的合理请求头
        headers.setdefault("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
        headers.setdefault("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8")
        headers.setdefault("Accept-Encoding", "gzip, deflate, br")
        headers.setdefault("DNT", "1")
        headers.setdefault("Upgrade-Insecure-Requests", "1")

        return headers


# 全局单例
_rotator: Optional[IdentityRotator] = None


def get_identity_rotator() -> IdentityRotator:
    """获取全局身份随机化单例。"""
    global _rotator
    if _rotator is None:
        _rotator = IdentityRotator()
    return _rotator
