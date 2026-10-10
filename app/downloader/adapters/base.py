"""Shared download adapter contract: return additional items, not template items."""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.base.http import DownloadResponse
from app.models.template import SiteTemplate


@dataclass(frozen=True, slots=True)
class DownloadItem:
    url: str
    filename: str
    asset_key: str
    attachment: bool = False


Filename = Callable[[str, str], str]
DownloadHandler = Callable[[dict[str, Any], SiteTemplate, Filename], list[DownloadItem]]
ResponseValidator = Callable[[DownloadItem, DownloadResponse], None]
