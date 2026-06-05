"""本地文件代理源适配器。

从本地文件读取代理列表，支持注释行和空行过滤，每行一个代理 URL。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.anti_crawl.adapters.base import ProxyInfo, ProxySourceAdapter

logger = logging.getLogger(__name__)


class FileProxySourceAdapter(ProxySourceAdapter):
    """本地文件代理源适配器。

    从本地文件读取代理列表，每行一个代理 URL。
    支持以 # 开头的注释行和空行。

    配置项::

        {
            "file_path": "data/proxies.txt",
            "encoding": "utf-8",
        }
    """

    ADAPTER_NAME = "file_source"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)

    @property
    def name(self) -> str:
        return self.ADAPTER_NAME

    def validate_config(self) -> bool:
        """验证 file_path 配置存在。"""
        return bool(self._config.get("file_path"))

    async def fetch(self) -> list[ProxyInfo]:
        """从文件读取代理列表。

        Returns:
            ProxyInfo 对象列表。
        """
        file_path = self._config.get("file_path", "")
        encoding = self._config.get("encoding", "utf-8")

        path = Path(file_path)
        if not path.exists():
            logger.warning("FileProxySourceAdapter: file not found: %s", path)
            return []

        if not path.is_file():
            logger.warning("FileProxySourceAdapter: path is not a file: %s", path)
            return []

        try:
            content = path.read_text(encoding=encoding)
        except (OSError, UnicodeDecodeError) as e:
            logger.error("FileProxySourceAdapter: failed to read file %s: %s", path, e)
            return []

        proxies: list[ProxyInfo] = []
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            
            # 完全过滤socket协议，只保留http/https
            if line.startswith('socks://') or line.startswith('socks5://') or line.startswith('socks4://') or line.startswith('socket://'):
                continue
            
            # 确保协议是 http 或 https
            if not (line.startswith('http://') or line.startswith('https://')):
                continue
            
            proxies.append(ProxyInfo(line))

        logger.info("FileProxySourceAdapter: loaded %d proxies from %s", len(proxies), path)
        return proxies