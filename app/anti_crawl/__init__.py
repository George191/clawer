"""反爬智能层 - Anti-Crawl Intelligence Layer.

提供代理池管理、请求延迟策略、身份随机化三大能力。
所有功能默认关闭，通过 settings.py 配置启用，保持向后兼容。

代理池采用适配器模式，框架核心与具体代理源实现解耦，
支持通过配置动态切换和组合多个代理源。
"""

from app.anti_crawl.adapters.base import ProxyInfo
from app.anti_crawl.proxy_pool import (
    ProxyPool,
    get_proxy_pool,
)
from app.anti_crawl.request_delayer import RequestDelayer
from app.anti_crawl.identity_rotator import IdentityRotator

__all__ = [
    "ProxyInfo",
    "ProxyPool",
    "get_proxy_pool",
    "RequestDelayer",
    "IdentityRotator",
]