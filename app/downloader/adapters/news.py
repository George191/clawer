"""Downloads for resources extracted from news bodies."""
from .base import DownloadItem


def download(record, template, filename):
    """Return only extracted resources; template downloads run in the worker."""
    items = []
    for field in ("attachments", "images", "videos", "audios"):
        values = record.get(field)
        if not isinstance(values, list):
            continue
        for index, value in enumerate(values):
            url = value.get("url") if isinstance(value, dict) else value
            if not url:
                continue
            items.append(DownloadItem(
                url=str(url),
                filename=filename(str(url), f"_{index:05d}"),
                asset_key=(f"assets.{field}.{index}.url" if isinstance(value, dict) else f"assets.{field}.{index}"),
                attachment=field == "attachments",
            ))
    return items
