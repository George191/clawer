from __future__ import annotations

from typing import Any

import pytest

from app.anti_crawl.adapters.base import ProxyInfo, ProxySourceAdapter
from app.anti_crawl.adapters.zdopen import ZdopenAPIAdapter
from app.anti_crawl.proxy_pool import ProxyPool
from app.config.settings import settings
from app.downloader.http_client import DownloadError, HttpClient


class _ProxySource(ProxySourceAdapter):
    @property
    def name(self) -> str:
        return "test"

    def validate_config(self) -> bool:
        return True

    async def fetch(self) -> list[ProxyInfo]:
        return []


@pytest.mark.asyncio
async def test_adapter_failure_does_not_remove_proxy_globally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "anti_crawl_enabled", True)
    pool = ProxyPool()
    pool.register_adapter(_ProxySource())
    proxy = ProxyInfo("http://proxy.example:8080")
    pool._proxies = [proxy]
    pool._healthy = [proxy]
    pool._leases = {1: proxy}

    await pool.mark_failure(proxy.url, "google_patent")

    assert pool._proxies == [proxy]
    assert pool._healthy == [proxy]
    assert pool._leases == {1: proxy}
    assert pool._adapter_failures == {proxy.url: {"google_patent"}}


def test_jump_transport_failure_does_not_poison_exit_proxy() -> None:
    error = RuntimeError("jump proxy connection failed")

    assert not HttpClient._should_mark_proxy_failure(error, "socks5://127.0.0.1:12012")
    assert HttpClient._should_mark_proxy_failure(error, None)
    assert HttpClient._should_mark_proxy_failure(
        DownloadError("https://example.test", 502),
        "socks5://127.0.0.1:12012",
    )


class _Response:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _Client:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = responses

    async def get(self, url: str) -> _Response:
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_zdopen_rate_limit_waits_then_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = ZdopenAPIAdapter({"url": "https://proxy-api.example.test"})
    client = _Client(
        [
            _Response({"code": "12002", "msg": "rate limited"}),
            _Response(
                {
                    "code": "10001",
                    "data": {
                        "proxy_list": [
                            {"ip": "192.0.2.1", "port": 8080, "protocol": "http"}
                        ]
                    },
                }
            ),
        ]
    )
    waits: list[int] = []

    async def get_client() -> _Client:
        return client

    async def sleep(seconds: int) -> None:
        waits.append(seconds)

    monkeypatch.setattr(adapter, "_get_client", get_client)
    monkeypatch.setattr("app.anti_crawl.adapters.zdopen.asyncio.sleep", sleep)

    proxies = await adapter.fetch()

    assert waits == [1]
    assert [proxy.url for proxy in proxies] == ["http://192.0.2.1:8080"]
