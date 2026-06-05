"""代理源适配器模块 - Proxy Source Adapters.

提供统一的代理源适配器接口及内置实现。
"""

from app.anti_crawl.adapters.base import ProxySourceAdapter
from app.anti_crawl.adapters.zdopen import ZdopenAPIAdapter
from app.anti_crawl.adapters.file_source import FileProxySourceAdapter

__all__ = [
    "ProxySourceAdapter",
    "ZdopenAPIAdapter",
    "FileProxySourceAdapter",
]