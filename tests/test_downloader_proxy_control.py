from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.config.settings import settings
from app.downloader import http_client as http_client_module
from app.downloader.http_client import HttpClient


@pytest.mark.asyncio
async def test_download_proxy_can_be_disabled_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "download_use_proxy", False)
    monkeypatch.setattr(settings, "tunnel_proxy_url", "http://proxy.test:8080")
    proxy_pool = Mock(enabled=True)
    proxy_pool.lease_proxy = AsyncMock(return_value="http://pool.test:8080")
    monkeypatch.setattr(http_client_module, "_proxy_pool", proxy_pool)
    monkeypatch.setattr(http_client_module, "_rotator", None)
    monkeypatch.setattr(http_client_module, "_delayer", None)
    init_anti_crawl = Mock()
    monkeypatch.setattr(http_client_module, "_init_anti_crawl", init_anti_crawl)

    response = Mock(status_code=200, content=b"asset")
    session = AsyncMock()
    session.request.return_value = response
    client = HttpClient()
    client._get_download_client = AsyncMock(return_value=session)

    data = await client.download_bytes("https://assets.test/file.png")

    assert data == b"asset"
    init_anti_crawl.assert_called_once_with(use_proxy=False)
    proxy_pool.lease_proxy.assert_not_awaited()
    client._get_download_client.assert_awaited_once_with(
        id(asyncio.current_task()),
        None,
        pre_proxy_url=None,
    )


@pytest.mark.asyncio
async def test_page_request_still_uses_configured_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy_url = "http://proxy.test:8080"
    monkeypatch.setattr(settings, "tunnel_proxy_url", proxy_url)
    monkeypatch.setattr(settings, "http_debug_proxy_ip", False)
    monkeypatch.setattr(http_client_module, "_proxy_pool", None)
    monkeypatch.setattr(http_client_module, "_rotator", None)
    monkeypatch.setattr(http_client_module, "_delayer", None)
    init_anti_crawl = Mock()
    monkeypatch.setattr(http_client_module, "_init_anti_crawl", init_anti_crawl)

    response = Mock(status_code=200, text="page")
    session = AsyncMock()
    session.request.return_value = response
    context = AsyncMock()
    context.__aenter__.return_value = session
    client = HttpClient()
    client._create_client = AsyncMock(return_value=context)

    text = await client.request_page(
        "https://patents.google.com/xhr/query",
        anti_crawl_enabled=True,
    )

    assert text == "page"
    init_anti_crawl.assert_called_once_with()
    client._create_client.assert_awaited_once_with(
        proxy_url,
        no_timeout=False,
        pre_proxy_url=None,
    )
