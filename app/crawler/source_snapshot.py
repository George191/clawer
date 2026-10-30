"""Conditional source reads; validators commit with the successful crawl only."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.logger import get_logger

logger = get_logger(__name__)


async def request_source_snapshot(
    client: Any,
    store: Any,
    url: str,
    config: Any,
    *,
    binary: bool = False,
    use_proxy: bool | None = None,
    identity_config: Any = None,
    conditional: bool = True,
    **kwargs: Any,
) -> str | bytes | None:
    """Return None only when this exact, previously committed source is unchanged.

    Callers must continue checking other files/pages when one is unchanged.
    No response bodies are retained in Redis.
    """
    if store is None:
        if binary:
            return await client.download_bytes(url, config, use_proxy=use_proxy)
        return await client.request_page(url, config, **kwargs)

    identity_request = identity_config or config
    identity = json.dumps(
        [url, identity_request.method, identity_request.params, identity_request.json_data, identity_request.form_data,
         identity_request.headers, identity_request.cookies],
        sort_keys=True, default=str,
    )
    previous = await store.get_metadata(identity) or {}
    headers = dict(config.headers)
    if conditional and previous.get("etag"):
        headers["If-None-Match"] = previous["etag"]
    elif conditional and previous.get("last_modified"):
        headers["If-Modified-Since"] = previous["last_modified"]
    request = config.model_copy(update={"headers": headers})
    if binary:
        response = await client.download_response(url, request, use_proxy=use_proxy)
    else:
        response = await client.request_page(url, request, return_response=True, **kwargs)
    if response.status_code == 304:
        if not previous.get("hash"):
            raise ValueError("Source returned 304 without a committed content fingerprint")
        logger.info("Incremental source unchanged: HTTP 304")
        return None
    if response.status_code != 200:
        raise ValueError(f"Snapshot source returned unexpected HTTP {response.status_code}")
    digest = hashlib.sha256(response.data).hexdigest()
    await store.set_metadata(identity, {
        "etag": response.headers.get("etag", ""),
        "last_modified": response.headers.get("last-modified", ""),
        "hash": digest,
    })
    if digest == previous.get("hash"):
        logger.info("Incremental source unchanged: content fingerprint matched")
        return None
    if binary:
        return response.data
    charset = re.search(r"charset=([^;\s]+)", response.headers.get("content-type", ""), re.I)
    encoding = config.encoding or (charset[1].strip('"') if charset else "utf-8")
    try:
        return response.data.decode(encoding, errors="replace")
    except LookupError:
        return response.data.decode("utf-8-sig")
