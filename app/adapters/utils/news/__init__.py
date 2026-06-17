"""新闻适配器公共基类与工具导出。"""

from app.adapters.utils.news.news_base import (
    NewsBaseAdapter,
    _ATTACHMENT_EXTENSIONS,
    _SOCIAL_DOMAINS,
)

__all__ = [
    "NewsBaseAdapter",
    "_ATTACHMENT_EXTENSIONS",
    "_SOCIAL_DOMAINS",
]
