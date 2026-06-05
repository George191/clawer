"""Zdopen 免费代理 API 适配器。

适配 http://www.zdopen.com/FreeProxy/Get/ 接口，解析其返回的 JSON 代理列表。
支持按需配置代理协议（http/https/socks5）和境内/境外过滤。
"""

from __future__ import annotations

import logging
from typing import Any

from curl_cffi import requests as curl_requests

from app.anti_crawl.adapters.base import ProxyInfo, ProxySourceAdapter

logger = logging.getLogger(__name__)

# 默认 API 基础 URL
DEFAULT_ZDPEN_API_URL = "http://www.zdopen.com/FreeProxy/Get/"


class ZdopenAPIAdapter(ProxySourceAdapter):
    """Zdopen 免费代理 API 适配器。

    从 Zdopen 提供的免费代理 API 获取代理列表。
    API 返回 JSON 数组，每个元素包含 ip、port、protocol、adr、level 字段。

    配置项::

        {
            "url": "http://www.zdopen.com/FreeProxy/Get/?app_id=...&akey=...",
            "app_id": "202606031256055320",
            "akey": "5c8b84ddbf22acbc",
            "dalu": 0,           # 0=境外, 1=境内
            "return_type": 3,    # 返回格式: 1=文本, 2=xml, 3=json
            "timeout": 10,       # 请求超时(秒)
            "protocol_filter": "http",  # 协议过滤: http/https/socks4/socks5, 空表示不过滤
        }
    """

    # 适配器名称
    ADAPTER_NAME = "zdopen_api"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self._http_client: curl_requests.AsyncSession | None = None

    @property
    def name(self) -> str:
        return self.ADAPTER_NAME

    def validate_config(self) -> bool:
        """验证必须提供 url 或 (app_id + akey)。"""
        return bool(
            self._config.get("url")
            or (self._config.get("app_id") and self._config.get("akey"))
        )

    def _build_api_url(self) -> str:
        """构建 API 请求 URL。"""
        if direct_url := self._config.get("url"):
            return direct_url

        params = {
            "app_id": self._config["app_id"],
            "akey": self._config["akey"],
            "dalu": self._config.get("dalu", 0),
            "return_type": self._config.get("return_type", 3),
        }
        query = "&".join(f"{k}={v}" for k, v in params.items())
        return f"{DEFAULT_ZDPEN_API_URL}?{query}"

    async def _get_client(self) -> curl_requests.AsyncSession:
        """获取或创建 HTTP 客户端。"""
        if self._http_client is None:
            timeout = self._config.get("timeout", 10)
            self._http_client = curl_requests.AsyncSession(
                timeout=timeout,
                impersonate="chrome120",
            )
        return self._http_client

    async def fetch(self) -> list[ProxyInfo]:
        """从 Zdopen API 获取代理列表。

        Returns:
            ProxyInfo 对象列表。
        """
        if not self.validate_config():
            logger.error("ZdopenAPIAdapter: invalid config, missing url or app_id+akey")
            return []

        api_url = self._build_api_url()
        logger.info("Fetching proxies from Zdopen API: %s", api_url)

        try:
            client = await self._get_client()
            response = await client.get(api_url)
            response.raise_for_status()
            data = response.json()
        except curl_requests.errors.RequestsError as e:
            logger.error("ZdopenAPIAdapter: HTTP request failed: %s", e)
            return []
        except ValueError as e:
            logger.error("ZdopenAPIAdapter: JSON parse failed: %s", e)
            return []

        proxy_list = self._extract_proxy_list(data)
        proxies = self._parse_proxy_items(proxy_list)

        logger.info("ZdopenAPIAdapter: fetched %d proxies", len(proxies))
        return proxies

    def _extract_proxy_list(self, data: Any) -> list[dict]:
        """从 API 响应中提取代理条目列表。

        Zdopen API 成功响应格式::

            {
                "code": "10001",
                "msg": "...",
                "data": {
                    "count": 100,
                    "proxy_list": [...]
                }
            }

        也支持直接数组格式和其他嵌套格式，自动递归查找。

        Args:
            data: API 响应的解析结果。

        Returns:
            代理字典列表。如果响应指示错误或无法解析，返回空列表。
        """
        if isinstance(data, list):
            return data

        if isinstance(data, dict):
            # 检查是否是错误响应
            code = data.get("code", "")
            if code and code != "10001":
                msg = data.get("msg", "Unknown error")
                logger.warning("ZdopenAPI returned error code=%s: %s", code, msg)
                return []

            # 直接查找包含 ip 字段的列表
            for key, value in data.items():
                if isinstance(value, list) and value:
                    first = value[0]
                    if isinstance(first, dict) and "ip" in first:
                        logger.debug("Found proxy list in key: '%s'", key)
                        return value

            # 递归查找嵌套结构，如 {"data": {"proxy_list": [...]}}
            for key, value in data.items():
                if isinstance(value, dict):
                    result = self._extract_proxy_list(value)
                    if result:
                        return result
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, dict):
                            result = self._extract_proxy_list(item)
                            if result:
                                return result

        logger.warning("ZdopenAPIAdapter: unexpected response format: %s", type(data))
        if isinstance(data, dict):
            logger.debug("Response keys: %s", list(data.keys()))
        return []

    def _parse_proxy_items(self, items: list[dict]) -> list[ProxyInfo]:
        """将代理字典列表转换为 ProxyInfo 列表。

        Args:
            items: 代理字典列表，每项含 ip、port、protocol 字段。

        Returns:
            ProxyInfo 对象列表。
        """
        protocol_filter = self._config.get("protocol_filter", "")

        proxies: list[ProxyInfo] = []
        for item in items:
            ip = item.get("ip", "").strip()
            port = item.get("port", "")
            protocol = item.get("protocol", "http").strip().lower()

            if not ip or not port:
                continue
            
            # 完全过滤socket协议，只保留http/https
            if protocol.startswith('socks') or protocol.startswith('socket'):
                continue

            # 协议过滤
            if protocol_filter and protocol != protocol_filter.lower():
                continue
            
            # 确保协议是 http 或 https
            if protocol not in ['http', 'https']:
                continue

            proxy_url = f"{protocol}://{ip}:{port}"
            proxies.append(ProxyInfo(proxy_url))

        return proxies

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None