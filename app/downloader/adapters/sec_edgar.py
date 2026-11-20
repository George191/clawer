"""SEC organization/filing collection routing and additional downloads."""
from .base import DownloadItem

COLLECTION_ROUTES = {
    "organization_sec_edgar": ("sec_edgar", "organization"),
    "filing_sec_edgar": ("sec_edgar", "filing"),
}


def download(record, template, filename) -> list[DownloadItem]:
    """SEC resources are covered by template.download; no additional items."""
    return []
