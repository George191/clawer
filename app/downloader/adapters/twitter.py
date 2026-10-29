"""Twitter account/tweet collection routing and additional downloads."""
from .base import DownloadItem

COLLECTION_ROUTES = {
    "account_twitter": ("twitter", "account"),
    "tweet_twitter": ("twitter", "tweet"),
}


def download(record, template, filename) -> list[DownloadItem]:
    """Scoped account URLs and recursive tweet media are covered by template.download."""
    return []
