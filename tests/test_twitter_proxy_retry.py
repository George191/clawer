"""Exercise the real SDK queue and SQLite locks without contacting X."""

import asyncio
import json
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("twscrape")
from curl_cffi import CurlOpt
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.errors import RequestsError
from twscrape import account, http, queue_client, xclid
from twscrape.accounts_pool import AccountsPool, NoAccountError

from app.adapters import twitter
from app.anti_crawl.adapters.base import ProxyInfo
from app.anti_crawl.proxy_pool import ProxyPool


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch):
    # The released adapter patches the SDK once per worker. Restore it per test.
    for owner, name in (
        (account, "_make_http_client"),
        (xclid, "_make_http_client"),
        (queue_client.Ctx, "retry"),
        (queue_client.QueueClient, "_close_ctx"),
        (queue_client.XClIdGenStore, "get"),
    ):
        monkeypatch.setattr(owner, name, owner.__dict__[name])
    for name in ("_spider_transport_context", "_spider_transport_version"):
        monkeypatch.delattr(http, name, raising=False)
    monkeypatch.setattr(queue_client.XClIdGenStore, "items", {})
    monkeypatch.setattr(queue_client.telemetry, "capture", Mock())
    monkeypatch.setattr(twitter.settings, "anti_crawl_enabled", True)
    monkeypatch.setattr(twitter.settings, "tunnel_proxy_url", "")
    monkeypatch.setattr(twitter.settings, "http_proxy", "")
    monkeypatch.setattr(twitter.settings, "proxy_pre_proxy_url", "socks5h://jump.test:1080")
    monkeypatch.setattr(twitter.settings, "proxy_rotation", "round_robin")


async def make_adapter(tmp_path, monkeypatch):
    db = str(tmp_path / "accounts.db")
    accounts = AccountsPool(db, raise_when_no_account=True)
    await accounts.add_account_cookies("test_collector", "auth_token=test; ct0=test")
    adapter = twitter.TwitterAdapter("https://x.com")
    await adapter.on_before_crawl(SimpleNamespace(_param_values={
        "username": "COMSPOC_OPS", "limit": "1", "accounts_db": db,
    }))
    pool = ProxyPool()
    pool._adapters = [Mock()]
    pool._loaded = True
    pool._proxies = [ProxyInfo("http://proxy-a.test:80"), ProxyInfo("http://proxy-b.test:80")]
    pool._healthy = list(pool._proxies)
    monkeypatch.setattr(twitter, "get_proxy_pool", lambda: pool)
    monkeypatch.setattr(queue_client.XClIdGen, "create", AsyncMock(
        return_value=SimpleNamespace(calc=lambda *args: "test-transaction-id"),
    ))
    return adapter, accounts, pool


@pytest.mark.parametrize("existing_context", [False, True])
@pytest.mark.parametrize("error_code", [7, 28])
def test_first_transport_failure_rotates_without_cooldown(tmp_path, monkeypatch, existing_context, error_code):
    async def scenario():
        if existing_context:
            # An earlier MinIO adapter initialized this in the persistent worker.
            monkeypatch.setattr(http, "_spider_transport_context", ContextVar("old-twitter", default=None), raising=False)
        adapter, accounts, pool = await make_adapter(tmp_path, monkeypatch)
        calls = []

        async def request(session, method, url, **kwargs):
            calls.append((session.proxies["all"], session.curl_options.get(CurlOpt.PRE_PROXY)))
            raise RequestsError("simulated proxy failure", code=error_code)

        monkeypatch.setattr(AsyncSession, "request", request)
        sleep = AsyncMock(side_effect=AssertionError("Transport retry must not sleep"))
        monkeypatch.setattr(asyncio, "sleep", sleep)
        cooldown = AsyncMock(wraps=adapter._api.pool.lock_until)
        monkeypatch.setattr(adapter._api.pool, "lock_until", cooldown)
        for attempt in range(2):
            with pytest.raises(twitter._TwitterProxyRetryError) as failed:
                await adapter.request_list_page("", None, anti_crawl_enabled=True)
            assert await adapter.on_error(failed.value, 1, attempt) == "rotate_proxy"
            acc = await accounts.get("test_collector")
            assert acc.active and "UserByScreenName" not in acc.locks
            assert not pool._leases
        assert [call[0] for call in calls] == [p.url for p in pool._proxies]
        assert all(call[1] == "socks5h://jump.test:1080" for call in calls)
        assert all(pool._adapter_failures[p.url] == {"twitter"} for p in pool._proxies)
        cooldown.assert_not_awaited()
        sleep.assert_not_awaited()
        assert await adapter.on_error(twitter._TwitterProxyRetryError(), 1, 25) == "rotate_proxy"

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["rate_limit", "expired_session"])
def test_account_restrictions_are_preserved(tmp_path, monkeypatch, failure):
    async def scenario():
        adapter, accounts, pool = await make_adapter(tmp_path, monkeypatch)
        headers = {"content-type": "application/json"}
        if failure == "rate_limit":
            headers.update({"x-rate-limit-remaining": "0", "x-rate-limit-reset": "4102444800"})
            status, payload = 429, {"errors": [{"code": 88, "message": "Rate limit exceeded"}]}
        else:
            status, payload = 401, {"errors": [{"code": 32, "message": "Could not authenticate you"}]}
        response = SimpleNamespace(status_code=status, headers=headers, json=lambda: payload, text=json.dumps(payload))
        request = AsyncMock(return_value=response)
        monkeypatch.setattr(AsyncSession, "request", request)
        with pytest.raises(NoAccountError):
            await adapter.request_list_page("", None, anti_crawl_enabled=True)
        acc = await accounts.get("test_collector")
        if failure == "rate_limit":
            assert acc.active and acc.locks["UserByScreenName"].year == 2100
        else:
            assert not acc.active and "authenticate" in acc.error_msg
        request.assert_awaited_once()
        assert not pool._adapter_failures

    asyncio.run(scenario())


def test_no_rotation_context_keeps_sdk_backoff(monkeypatch):
    async def scenario():
        with twitter._twitter_transport(None, rotate_on_network=True):
            pass
        sleep = AsyncMock()
        monkeypatch.setattr(asyncio, "sleep", sleep)
        ctx = queue_client.Ctx(Mock(), Mock())
        assert await ctx.retry(queue_client.FailKind.TRANSPORT) is True
        sleep.assert_awaited_once_with(2)
        sleep.reset_mock()
        with twitter._twitter_transport(None, rotate_on_network=True):
            ctx = queue_client.Ctx(Mock(), Mock())
            assert await ctx.retry(queue_client.FailKind.LOADSHED) is True
        sleep.assert_awaited_once_with(2)

    asyncio.run(scenario())


def test_xclid_failure_does_not_lock_account(tmp_path, monkeypatch):
    async def scenario():
        adapter, accounts, pool = await make_adapter(tmp_path, monkeypatch)
        monkeypatch.setattr(queue_client.XClIdGen, "create", AsyncMock(side_effect=xclid.XClIdParseError("X web scripts not found")))
        with pytest.raises(xclid.XClIdParseError) as failed:
            await adapter.request_list_page("", None, anti_crawl_enabled=True)
        assert await adapter.on_error(failed.value, 1, 0) == "rotate_proxy"
        acc = await accounts.get("test_collector")
        assert acc.active and not acc.locks
        assert pool._adapter_failures[pool._proxies[0].url] == {"twitter"}

    asyncio.run(scenario())


def test_next_proxy_succeeds_with_same_account(tmp_path, monkeypatch):
    async def scenario():
        adapter, accounts, pool = await make_adapter(tmp_path, monkeypatch)
        routes = []

        async def request(session, method, url, **kwargs):
            routes.append(session.proxies["all"])
            if len(routes) == 1:
                raise RequestsError("simulated timeout", code=28)
            payload = {"data": {}}
            if url.endswith("/UserByScreenName"):
                payload = {"data": {"user": {"result": {
                    "__typename": "User", "rest_id": "123",
                    "core": {"screen_name": "COMSPOC_OPS", "name": "COMSPOC"},
                }}}}
            return SimpleNamespace(status_code=200, headers={}, text=json.dumps(payload), json=lambda: payload)

        monkeypatch.setattr(AsyncSession, "request", request)
        with pytest.raises(twitter._TwitterProxyRetryError) as failed:
            await adapter.request_list_page("", None, anti_crawl_enabled=True)
        assert await adapter.on_error(failed.value, 1, 0) == "rotate_proxy"
        result = await adapter.request_list_page("", None, anti_crawl_enabled=True)
        assert adapter._user_id == 123 and json.loads(result) == {"data": []}
        assert routes == [pool._proxies[0].url, pool._proxies[1].url, pool._proxies[1].url]
        acc = await accounts.get("test_collector")
        assert acc.active and not acc.locks
        assert acc.stats["UserByScreenName"] == 1
        assert acc.stats["UserTweetsAndReplies"] == 1

    asyncio.run(scenario())


def test_empty_proxy_pool_does_not_fall_back_to_direct(tmp_path, monkeypatch):
    async def scenario():
        adapter, accounts, pool = await make_adapter(tmp_path, monkeypatch)
        monkeypatch.setattr(pool, "lease_proxy", AsyncMock(return_value=None))
        request = AsyncMock(side_effect=AssertionError("No direct request expected"))
        monkeypatch.setattr(AsyncSession, "request", request)
        with pytest.raises(RuntimeError, match="proxy pool has no available proxy"):
            await adapter.request_list_page("", None, anti_crawl_enabled=True)
        request.assert_not_awaited()
        acc = await accounts.get("test_collector")
        assert not acc.locks and not acc.stats

    asyncio.run(scenario())
