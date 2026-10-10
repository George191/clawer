"""SEC company/filing collection routing and additional downloads."""
import re

from app.base.http import DownloadError, DownloadResponse

from .base import DownloadItem

ZSCALER_MARKERS = re.compile(
    rb"zscaler directory authentication"
    rb"|login\.zscaler(?:beta)?\.net"
    rb"|<!--\s*username\.html",
    re.IGNORECASE,
)

COLLECTION_ROUTES = {
    "sec_edgar_company": ("sec_edgar", "company"),
    "sec_edgar_filing": ("sec_edgar", "filing"),
}


def download(record, template, filename) -> list[DownloadItem]:
    """SEC resources are covered by template.download; no additional items."""
    return []


def validate_response(item: DownloadItem, response: DownloadResponse) -> None:
    """Reject SEC proxy authentication pages before uploading the file."""
    if ZSCALER_MARKERS.search(response.data[:32768]):
        raise DownloadError(item.url, response.status_code, "Zscaler authentication page")
