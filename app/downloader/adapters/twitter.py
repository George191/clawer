"""Twitter account/tweet collection routing and additional downloads."""
from .base import DownloadItem

COLLECTION_ROUTES = {
    "tw_account": ("twitter", "account"),
    "tw_tweet": ("twitter", "tweet"),
}


def download(record, template, filename) -> list[DownloadItem]:
    """Scoped account URLs and recursive tweet media are covered by template.download."""
    return []
