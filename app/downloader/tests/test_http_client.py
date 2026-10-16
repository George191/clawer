from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from curl_cffi.requests import Response
from curl_cffi.requests.exceptions import ConnectionError, HTTPError, ProxyError, Timeout

from app.downloader import http_client
from app.downloader.http_client import DownloadError, HttpClient

ASSET_URL = "https://example.com/asset.pdf"
PROXY_URL = "http://proxy.example.com:8080"
PRE_PROXY_URL = "socks5://jump.example.com:1080"


@pytest.fixture
def download_client(monkeypatch):
    pool = SimpleNamespace(
        enabled=True,
        lease_proxy=AsyncMock(return_value=PROXY_URL),
        release_proxy=AsyncMock(),
        mark_failure=AsyncMock(),
        mark_success=AsyncMock(),
    )
    session = SimpleNamespace(request=AsyncMock(), close=AsyncMock())
    client = HttpClient()
    monkeypatch.setattr(client, "_create_client", AsyncMock(return_value=session))
    monkeypatch.setattr(http_client, "_init_anti_crawl", lambda **kwargs: None)
    monkeypatch.setattr(http_client, "_proxy_pool", pool)
    monkeypatch.setattr(http_client, "_rotator", None)
    monkeypatch.setattr(http_client, "_delayer", None)
    monkeypatch.setattr(http_client.settings, "tunnel_proxy_url", "")
    monkeypatch.setattr(http_client.settings, "proxy_pre_proxy_url", "")
    monkeypatch.setattr(http_client.settings, "http_retry_on_statuses", [429, 500, 502, 503, 504])
    return client, session, pool


@pytest.mark.parametrize("status_code", [403, 404, 429, 503])
async def test_http_error_retains_proxy(download_client, status_code):
    client, session, pool = download_client
    response = Response()
    response.status_code = status_code
    response.ok = False
    response.url = ASSET_URL
    session.request.return_value = response

    with pytest.raises((HTTPError, DownloadError)) as exc_info:
        await client.download_response(ASSET_URL, use_proxy=True)

    assert client._error_status_code(exc_info.value) == status_code
    pool.mark_failure.assert_not_awaited()
    pool.release_proxy.assert_not_awaited()
    session.close.assert_not_awaited()


@pytest.mark.parametrize("use_proxy", [False, True])
async def test_transport_error_discards_session(download_client, use_proxy):
    client, session, pool = download_client
    response = Response()
    response.status_code = 0
    error = ConnectionError("connection reset", response=response)
    session.request.side_effect = error

    with pytest.raises(ConnectionError):
        await client.download_response(ASSET_URL, use_proxy=use_proxy)

    session.close.assert_awaited_once()
    assert not client._download_clients
    if use_proxy:
        pool.mark_failure.assert_awaited_once_with(PROXY_URL)
        pool.release_proxy.assert_awaited_once()
    else:
        pool.mark_failure.assert_not_awaited()
        pool.release_proxy.assert_not_awaited()


@pytest.mark.parametrize("error_type", [ConnectionError, ProxyError])
async def test_jump_host_only_blames_explicit_proxy_error(
    download_client, monkeypatch, error_type
):
    client, session, pool = download_client
    monkeypatch.setattr(http_client.settings, "proxy_pre_proxy_url", PRE_PROXY_URL)
    response = Response()
    response.status_code = 0
    session.request.side_effect = error_type("route failed", response=response)

    with pytest.raises(error_type):
        await client.download_response(ASSET_URL, use_proxy=True)

    session.close.assert_awaited_once()
    assert not client._download_clients
    if error_type is ProxyError:
        pool.mark_failure.assert_awaited_once_with(PROXY_URL)
        pool.release_proxy.assert_awaited_once()
    else:
        pool.mark_failure.assert_not_awaited()
        pool.release_proxy.assert_not_awaited()


@pytest.mark.parametrize("pre_proxy", ["", PRE_PROXY_URL])
@pytest.mark.parametrize("error_type", [ConnectionError, Timeout, HTTPError])
async def test_body_transport_error_discards_session(
    download_client, monkeypatch, pre_proxy, error_type
):
    client, session, pool = download_client
    monkeypatch.setattr(http_client.settings, "proxy_pre_proxy_url", pre_proxy)
    response = Response()
    response.status_code = 200
    session.request.side_effect = error_type("body transfer failed", response=response)

    with pytest.raises(error_type):
        await client.download_response(ASSET_URL, use_proxy=True)

    session.close.assert_awaited_once()
    assert not client._download_clients
    if pre_proxy:
        pool.mark_failure.assert_not_awaited()
        pool.release_proxy.assert_not_awaited()
    else:
        pool.mark_failure.assert_awaited_once_with(PROXY_URL)
        pool.release_proxy.assert_awaited_once()


async def test_local_error_does_not_blame_proxy(download_client):
    client, session, pool = download_client
    session.request.side_effect = ValueError("invalid URL")

    with pytest.raises(ValueError):
        await client.download_response(ASSET_URL, use_proxy=True)

    pool.mark_failure.assert_not_awaited()
    pool.release_proxy.assert_not_awaited()


async def test_explicit_proxy_http_error_blames_proxy(download_client, monkeypatch):
    client, session, pool = download_client
    monkeypatch.setattr(http_client.settings, "proxy_pre_proxy_url", PRE_PROXY_URL)
    response = Response()
    response.status_code = 407
    response.ok = False
    session.request.side_effect = ProxyError("proxy authentication failed", response=response)

    with pytest.raises(ProxyError):
        await client.download_response(ASSET_URL, use_proxy=True)

    session.close.assert_awaited_once()
    pool.mark_failure.assert_awaited_once_with(PROXY_URL)
    pool.release_proxy.assert_awaited_once()


async def test_cancelled_download_discards_session(download_client):
    client, session, pool = download_client
    session.request.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await client.download_response(ASSET_URL, use_proxy=True)

    session.close.assert_awaited_once()
    assert not client._download_clients
    pool.mark_failure.assert_not_awaited()
