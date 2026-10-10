"""Register and resolve additional download adapters and collection routes."""
from typing import Any

from . import news, sec_edgar, twitter
from .base import DownloadHandler, ResponseValidator
from .base import DownloadItem as DownloadItem

_HANDLERS: dict[tuple[str | None, str], DownloadHandler] = {}
_RESPONSE_VALIDATORS: dict[str, ResponseValidator] = {"sec_edgar": sec_edgar.validate_response}
_COLLECTION_ROUTES = {**sec_edgar.COLLECTION_ROUTES, **twitter.COLLECTION_ROUTES}


def register_download_adapter(data_type: str, *, template_name: str | None = None):
    def register(handler: DownloadHandler) -> DownloadHandler:
        _HANDLERS[(template_name, data_type)] = handler
        return handler

    return register


register_download_adapter("news")(news.download)
for adapter in (sec_edgar, twitter):
    for template_name, data_type in adapter.COLLECTION_ROUTES.values():
        register_download_adapter(data_type, template_name=template_name)(adapter.download)


def collections_for_template(template_name: str | None) -> tuple[str | None, ...]:
    collections = tuple(collection for collection, route in _COLLECTION_ROUTES.items()
                        if route[0] == template_name)
    return collections or (template_name,)


def resolve_download_record(record: dict[str, Any]) -> tuple[str, str]:
    meta = record.get("_meta") or {}
    collection = meta.get("template") or ""
    template_name, fallback_type = _COLLECTION_ROUTES.get(collection, (collection, ""))
    return template_name, str(meta.get("data_type") or fallback_type).lower()


def get_download_adapter(record: dict[str, Any]) -> DownloadHandler | None:
    template_name, data_type = resolve_download_record(record)
    return _HANDLERS.get((template_name, data_type)) or _HANDLERS.get((None, data_type))


def get_download_response_validator(template_name: str) -> ResponseValidator | None:
    template_name, _ = _COLLECTION_ROUTES.get(template_name, (template_name, ""))
    return _RESPONSE_VALIDATORS.get(template_name)
