"""Shared download adapter contract: return additional items, not template items."""
from collections.abc import Callable
from dataclasses import dataclass
import re
from typing import Any

from app.base.http import DownloadError, DownloadResponse, FileDownloadResponse
from app.models.template import SiteTemplate


@dataclass(frozen=True, slots=True)
class DownloadItem:
    url: str
    filename: str
    asset_key: str
    attachment: bool = False
    version: Any = None


Filename = Callable[[str, str], str]
DownloadHandler = Callable[[dict[str, Any], SiteTemplate, Filename], list[DownloadItem]]

ZSCALER_MARKERS = re.compile(
    rb"zscaler directory authentication"
    rb"|login\.zscaler(?:beta)?\.net"
    rb"|<!--\s*username\.html",
    re.IGNORECASE,
)


def validate_response(item: DownloadItem, response: DownloadResponse | FileDownloadResponse) -> None:
    """Reject proxy authentication pages before uploading any downloaded file."""
    if ZSCALER_MARKERS.search(response.read_prefix(32768)):
        raise DownloadError(item.url, response.status_code, "Zscaler authentication page")
