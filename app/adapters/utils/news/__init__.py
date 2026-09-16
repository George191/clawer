"""新闻适配器公共基类与工具导出。"""

from app.adapters.utils.news.news_base import (
    NewsBaseAdapter,
    _SOCIAL_DOMAINS,
)
from app.adapters.utils.news.assets import _ATTACHMENT_EXTENSIONS, _IMAGE_EXTENSIONS

__all__ = [
    "NewsBaseAdapter",
    "_ATTACHMENT_EXTENSIONS",
    "_IMAGE_EXTENSIONS",
    "_SOCIAL_DOMAINS",
]
